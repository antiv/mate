/**
 * Trace Replay for the Visual Builder canvas.
 *
 * Plays back one traced chat turn (GET /dashboard/api/traces/replay) on the
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
     * What an agent is doing at `t` ms into the turn, or null if it does not
     * appear in the turn. state: 'pending' (not started yet), 'active', 'done'.
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

    /**
     * Props: canvasAgents (names on the canvas), initialSessionId, onFrame({turn, t} | null), onClose().
     */
    function ReplayBar(props) {
        const [sessions, setSessions] = useState(null);
        const [sessionId, setSessionId] = useState(props.initialSessionId || '');
        const [replay, setReplay] = useState(null);
        const [turnIdx, setTurnIdx] = useState(0);
        const [t, setT] = useState(0);
        const [playing, setPlaying] = useState(false);
        const [speed, setSpeed] = useState(1);
        const [error, setError] = useState(null);
        const onFrame = props.onFrame;

        useEffect(function () {
            fetch('/dashboard/api/traces/sessions?hours=168', { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    setSessions(d.sessions || []);
                    if (d.error) setError(d.error);
                })
                .catch(function (e) { setError(String(e)); setSessions([]); });
        }, []);

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
                    setTurnIdx(Math.max(0, (d.turns || []).length - 1));
                })
                .catch(function (e) { setError(String(e)); });
        }, [sessionId]);

        const turn = replay && replay.turns ? replay.turns[turnIdx] : null;
        const duration = turn ? turn.duration_ms : 0;

        useEffect(function () {
            onFrame(turn ? { turn: turn, t: t } : null);
        }, [turn, t, onFrame]);

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

        const togglePlay = useCallback(function () {
            if (!turn) return;
            if (!playing && t >= duration) setT(0);
            setPlaying(!playing);
        }, [turn, playing, t, duration]);

        const missing = turn
            ? Object.keys(turn.agents).filter(function (a) { return (props.canvasAgents || []).indexOf(a) === -1; })
            : [];

        const controls = h('div', { className: 'flex flex-wrap items-center gap-2' },
            h('i', { className: 'fas fa-film text-indigo-500' }),
            h('span', { className: 'font-semibold' }, 'Trace Replay'),
            h('select', {
                className: field + ' max-w-[16rem]',
                value: sessionId,
                'aria-label': 'Session',
                onChange: function (e) { setSessionId(e.target.value); },
            },
                h('option', { value: '' }, sessions == null ? 'Loading sessions…' : (sessions.length ? 'Choose a session…' : 'No traced sessions')),
                (sessions || []).map(function (s) {
                    return h('option', { key: s.session_id, value: s.session_id },
                        s.session_id.slice(0, 8) + ' · ' + s.agents.join(', ') + ' · ' + s.turns + ' turn' + (s.turns === 1 ? '' : 's'));
                }),
                sessionId && sessions && !sessions.some(function (s) { return s.session_id === sessionId; })
                    ? h('option', { value: sessionId }, sessionId) : null,
            ),
            replay && replay.turns.length > 1 ? h('select', {
                className: field,
                value: turnIdx,
                'aria-label': 'Turn',
                onChange: function (e) { setTurnIdx(Number(e.target.value)); setT(0); setPlaying(false); },
            }, replay.turns.map(function (tr, i) {
                return h('option', { key: tr.trace_id, value: i }, 'Turn ' + (i + 1) + ' · ' + formatMs(tr.duration_ms));
            })) : null,
            h('button', { type: 'button', className: btn, disabled: !turn, onClick: togglePlay, 'aria-label': playing ? 'Pause' : 'Play' },
                h('i', { className: 'fas ' + (playing ? 'fa-pause' : 'fa-play') })),
            h('button', { type: 'button', className: btn, disabled: !turn, onClick: function () { setPlaying(false); setT(0); }, 'aria-label': 'Restart' },
                h('i', { className: 'fas fa-undo' })),
            h('select', {
                className: field,
                value: speed,
                'aria-label': 'Speed',
                onChange: function (e) { setSpeed(Number(e.target.value)); },
            }, SPEEDS.map(function (s) { return h('option', { key: s, value: s }, s + '×'); })),
            h('input', {
                type: 'range',
                className: 'flex-1 min-w-[8rem]',
                min: 0,
                max: duration || 0,
                value: Math.min(t, duration || 0),
                disabled: !turn,
                'aria-label': 'Position in the turn',
                onChange: function (e) { setPlaying(false); setT(Number(e.target.value)); },
            }),
            h('span', { className: 'tabular-nums whitespace-nowrap' }, formatMs(t) + ' / ' + formatMs(duration)),
            h('button', { type: 'button', className: btn, onClick: props.onClose, 'aria-label': 'Close replay' },
                h('i', { className: 'fas fa-times' })),
        );

        let body = null;
        if (error) {
            body = h('div', { className: 'text-red-600 dark:text-red-400' }, error);
        } else if (sessions && !sessions.length && !sessionId) {
            body = h('div', { className: 'text-gray-500 dark:text-gray-400' },
                'Replay needs traced sessions: set OTEL_TRACING_ENABLED=true (with OTEL_TRACES_DB_EXPORT left on) and chat with an agent on the ADK runtime.');
        } else if (replay && !replay.turns.length) {
            body = h('div', { className: 'text-gray-500 dark:text-gray-400' }, 'No traced turns for this session.');
        } else if (turn) {
            body = h('div', { className: 'flex flex-col gap-1' },
                missing.length ? h('div', { className: 'text-amber-600 dark:text-amber-400' },
                    'Not on this canvas: ' + missing.join(', ') + ' (choose their project to see them).') : null,
                h('ol', { className: 'max-h-28 overflow-y-auto font-mono text-[11px] leading-5' },
                    turn.events.map(function (e, i) {
                        const state = e.start_ms > t ? 'text-gray-400 dark:text-gray-500'
                            : (e.end_ms > t ? 'text-indigo-600 dark:text-indigo-300 font-semibold' : 'text-gray-700 dark:text-gray-300');
                        return h('li', { key: i },
                            h('button', {
                                type: 'button',
                                className: 'text-left hover:underline ' + state + (e.status === 'ERROR' ? ' text-red-600 dark:text-red-400' : ''),
                                onClick: function () { setPlaying(false); setT(e.start_ms); },
                            }, formatMs(e.start_ms).padStart(9) + '  ' + eventLabel(e) + '  (' + formatMs(e.end_ms - e.start_ms) + ')'));
                    })),
            );
        }

        return h('div', { className: 'px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-indigo-50/60 dark:bg-gray-800 text-xs text-gray-700 dark:text-gray-200 flex flex-col gap-2' },
            controls, body);
    }

    window.MateTraceReplay = { ReplayBar: ReplayBar, agentStateAt: agentStateAt, formatMs: formatMs };
})();
