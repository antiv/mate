/**
 * Trace Replay for the Visual Builder canvas.
 *
 * Plays back a traced chat session (GET /dashboard/api/traces/replay) on the
 * agent graph: which agent ran when, which model answered (and whether it was
 * the fallback), which tools were called and how long each step took.
 *
 * Exposes window.MateTraceReplay = { ReplayBar, agentStateAt }. The builder
 * renders ReplayBar above the canvas and decorates its agent nodes with
 * agentStateAt(turn, agentName, timeMs).
 */

/* global window, fetch, URLSearchParams */

(function () {
    const React = window.React;
    if (!React) {
        return;
    }
    const h = React.createElement;
    const { useState, useEffect, useRef, useCallback } = React;

    const SPEEDS = [0.5, 1, 2, 5, 10];

    function formatMs(ms) {
        if (ms == null) return '';
        if (ms < 1000) return Math.round(ms) + ' ms';
        return (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + ' s';
    }

    /**
     * What an agent is doing at `t` ms into a timeline (a turn, or a session's
     * turns back to back), or null if it does not appear in it. state: 'pending' (not started yet), 'active', 'done'.
     */
    function agentStateAt(turn, agentName, t) {
        if (!turn || !turn.agents || !turn.agents[agentName]) {
            return null;
        }
        const own = turn.events.filter(function (e) { return e.agent === agentName; });
        const started = own.filter(function (e) { return e.start_ms <= t; });
        if (!started.length) {
            return { state: 'pending' };
        }
        const running = started.filter(function (e) { return e.end_ms > t; });
        const spans = own.filter(function (e) { return e.kind === 'agent'; });
        let busy = 0;
        spans.forEach(function (e) {
            if (e.start_ms <= t) busy += Math.min(e.end_ms, t) - e.start_ms;
        });
        const step = running.filter(function (e) { return e.kind !== 'agent'; }).pop();
        const fallbackCall = started.filter(function (e) { return e.kind === 'llm' && e.fallback; }).pop();
        return {
            state: running.length ? 'active' : 'done',
            busyMs: busy,
            error: started.some(function (e) { return e.status === 'ERROR' && e.end_ms <= t; }),
            step: step ? (step.kind === 'llm' ? (step.model || 'model call') : ('tool: ' + step.tool)) : null,
            fallback: fallbackCall ? { model: fallbackCall.model, primary: fallbackCall.primary_model } : null,
        };
    }

    function eventLabel(e) {
        if (e.kind === 'agent') return e.agent + ' runs';
        if (e.kind === 'llm') {
            return e.agent + ' → ' + (e.model || 'model') + (e.fallback ? ' (fallback for ' + (e.primary_model || 'primary') + ')' : '');
        }
        return e.agent + ' → tool ' + e.tool;
    }

    const btn = 'px-2 py-1 rounded bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 disabled:opacity-50';
    const field = 'border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-100 rounded px-1 py-0.5';

    function currentTurn(timeline, t) {
        let index = 0;
        (timeline.turns || []).forEach(function (m) { if (m.start_ms <= t) index = m.index; });
        return index;
    }

    /**
     * Plays a whole session: its turns back to back on one clock (the server's
     * `timeline`), so a hand-off from the root agent to a sub-agent in a later
     * turn plays on as one run.
     *
     * Props: projectId (offer only that project's sessions), canvasAgents (names
     * on the canvas), initialSessionId, onFrame({turn, t} | null), onClose().
     */
    function ReplayBar(props) {
        const [sessions, setSessions] = useState(null);
        const [sessionId, setSessionId] = useState(props.initialSessionId || '');
        const [replay, setReplay] = useState(null);
        const [t, setT] = useState(0);
        const [playing, setPlaying] = useState(false);
        const [speed, setSpeed] = useState(1);
        const [error, setError] = useState(null);
        const onFrame = props.onFrame;
        const projectId = props.projectId;

        useEffect(function () {
            setSessions(null);
            const query = { hours: 168 };
            if (projectId != null && projectId !== '') query.project_id = projectId;
            fetch('/dashboard/api/traces/sessions?' + new URLSearchParams(query), { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    setSessions(d.sessions || []);
                    if (d.error) setError(d.error);
                })
                .catch(function (e) { setError(String(e)); setSessions([]); });
        }, [projectId]);

        useEffect(function () {
            setReplay(null);
            setPlaying(false);
            setT(0);
            if (!sessionId) return;
            fetch('/dashboard/api/traces/replay?' + new URLSearchParams({ session_id: sessionId }), { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (d.detail) { setError(d.detail); return; }
                    setError(d.error || null);
                    setReplay(d);
                })
                .catch(function (e) { setError(String(e)); });
        }, [sessionId]);

        const timeline = replay && replay.timeline && replay.timeline.events.length ? replay.timeline : null;
        const duration = timeline ? timeline.duration_ms : 0;
        const turnCount = timeline ? timeline.turns.length : 0;

        useEffect(function () {
            onFrame(timeline ? { turn: timeline, t: t } : null);
        }, [timeline, t, onFrame]);

        useEffect(function () {
            return function () { onFrame(null); };
        }, [onFrame]);

        // Advance the clock while playing
        const last = useRef(null);
        useEffect(function () {
            if (!playing) { last.current = null; return undefined; }
            let raf;
            function tick(now) {
                if (last.current != null) {
                    const dt = (now - last.current) * speed;
                    setT(function (prev) { return Math.min(duration, prev + dt); });
                }
                last.current = now;
                raf = window.requestAnimationFrame(tick);
            }
            raf = window.requestAnimationFrame(tick);
            return function () { window.cancelAnimationFrame(raf); };
        }, [playing, speed, duration]);

        useEffect(function () {
            if (playing && t >= duration) setPlaying(false);
        }, [playing, t, duration]);

        // Keep the step being played in view in the list
        const listRef = useRef(null);
        useEffect(function () {
            const list = listRef.current;
            if (!list) return;
            const current = list.querySelectorAll('[data-started="true"]');
            const el = current.length ? current[current.length - 1] : null;
            if (el && (el.offsetTop < list.scrollTop || el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight)) {
                list.scrollTop = el.offsetTop - list.clientHeight / 2;
            }
        }, [t, timeline]);

        const togglePlay = useCallback(function () {
            if (!timeline) return;
            if (!playing && t >= duration) setT(0);
            setPlaying(!playing);
        }, [timeline, playing, t, duration]);

        const missing = timeline
            ? Object.keys(timeline.agents).filter(function (a) { return (props.canvasAgents || []).indexOf(a) === -1; })
            : [];

        const controls = h('div', { className: 'flex flex-wrap items-center gap-2' },
            h('i', { className: 'fas fa-film text-indigo-500' }),
            h('span', { className: 'font-semibold' }, 'Trace Replay'),
            h('select', {
                className: field + ' max-w-[18rem]',
                value: sessionId,
                'aria-label': 'Session',
                onChange: function (e) { setSessionId(e.target.value); },
            },
                h('option', { value: '' }, sessions == null ? 'Loading sessions…'
                    : (sessions.length ? 'Choose a session…' : 'No traced sessions in this project')),
                (sessions || []).map(function (s) {
                    return h('option', { key: s.session_id, value: s.session_id },
                        s.session_id.slice(0, 8) + ' · ' + s.agents.join(', ') + ' · ' + s.turns + ' turn' + (s.turns === 1 ? '' : 's'));
                }),
                sessionId && sessions && !sessions.some(function (s) { return s.session_id === sessionId; })
                    ? h('option', { value: sessionId }, sessionId) : null,
            ),
            h('button', { type: 'button', className: btn, disabled: !timeline, onClick: togglePlay, 'aria-label': playing ? 'Pause' : 'Play' },
                h('i', { className: 'fas ' + (playing ? 'fa-pause' : 'fa-play') })),
            h('button', { type: 'button', className: btn, disabled: !timeline, onClick: function () { setPlaying(false); setT(0); }, 'aria-label': 'Restart' },
                h('i', { className: 'fas fa-undo' })),
            h('select', {
                className: field,
                value: speed,
                'aria-label': 'Speed',
                onChange: function (e) { setSpeed(Number(e.target.value)); },
            }, SPEEDS.map(function (s) { return h('option', { key: s, value: s }, s + '×'); })),
            h('input', {
                type: 'range',
                className: 'flex-1 min-w-[6rem]',
                min: 0,
                max: duration || 0,
                value: Math.min(t, duration || 0),
                disabled: !timeline,
                'aria-label': 'Position in the session',
                onChange: function (e) { setPlaying(false); setT(Number(e.target.value)); },
            }),
            turnCount > 1 ? h('span', { className: 'whitespace-nowrap' },
                'Turn ' + (currentTurn(timeline, t) + 1) + ' / ' + turnCount) : null,
            h('span', { className: 'tabular-nums whitespace-nowrap' }, formatMs(t) + ' / ' + formatMs(duration)),
            h('button', { type: 'button', className: btn, onClick: props.onClose, 'aria-label': 'Close replay' },
                h('i', { className: 'fas fa-times' })),
        );

        let body = null;
        if (error) {
            body = h('div', { className: 'text-red-600 dark:text-red-400' }, error);
        } else if (sessions && !sessions.length && !sessionId) {
            body = h('div', { className: 'text-gray-500 dark:text-gray-400' },
                'No traced sessions with this project\'s agents. Replay needs OTEL_TRACING_ENABLED=true (with OTEL_TRACES_DB_EXPORT left on) on the ADK runtime, and a conversation held after it was turned on.');
        } else if (replay && !timeline) {
            body = h('div', { className: 'text-gray-500 dark:text-gray-400' }, 'No traced turns for this session.');
        } else if (timeline) {
            const items = [];
            timeline.events.forEach(function (e, i) {
                if (turnCount > 1 && (i === 0 || timeline.events[i - 1].turn !== e.turn)) {
                    const marker = timeline.turns[e.turn];
                    items.push(h('li', { key: 'turn-' + e.turn, className: 'mt-1 text-gray-500 dark:text-gray-400' },
                        h('button', {
                            type: 'button',
                            className: 'hover:underline',
                            onClick: function () { setPlaying(false); setT(marker.start_ms); },
                        }, '— Turn ' + (e.turn + 1) + ' (' + new Date(marker.started_at).toLocaleTimeString() + ') —')));
                }
                const state = e.start_ms > t ? 'text-gray-400 dark:text-gray-500'
                    : (e.end_ms > t ? 'text-indigo-600 dark:text-indigo-300 font-semibold' : 'text-gray-700 dark:text-gray-300');
                items.push(h('li', { key: i, 'data-started': e.start_ms <= t ? 'true' : 'false' },
                    h('button', {
                        type: 'button',
                        className: 'text-left hover:underline ' + state + (e.status === 'ERROR' ? ' text-red-600 dark:text-red-400' : ''),
                        onClick: function () { setPlaying(false); setT(e.start_ms); },
                    }, formatMs(e.start_ms).padStart(9) + '  ' + eventLabel(e) + '  (' + formatMs(e.end_ms - e.start_ms) + ')')));
            });
            body = h('div', { className: 'flex flex-col gap-1' },
                missing.length ? h('div', { className: 'text-amber-600 dark:text-amber-400' },
                    'Not on this canvas: ' + missing.join(', ') + '.') : null,
                h('ol', { ref: listRef, className: 'relative max-h-32 overflow-y-auto font-mono text-[11px] leading-5' }, items),
            );
        }

        return h('div', { className: 'px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-indigo-50/60 dark:bg-gray-800 text-xs text-gray-700 dark:text-gray-200 flex flex-col gap-2' },
            controls, body);
    }

    window.MateTraceReplay = { ReplayBar: ReplayBar, agentStateAt: agentStateAt, formatMs: formatMs };
})();
