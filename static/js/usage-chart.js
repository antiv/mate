/**
 * The charts of an agent's Usage tab (agent-page.js): tokens and cost over time, one
 * column per hour (a day's period) or per day, stacked by model.
 *
 *   const charts = mateUsageChart.create({ tokens: canvas, cost: canvas, table: element });
 *   charts.draw(byHour, hours);    // by_hour of GET /dashboard/api/usage/logs
 *   charts.colorOf(model);         // its colour, for the model's card, which is the legend
 *
 * The API counts by hour in UTC; the columns are the viewer's own hours and days, like
 * the times in the table. Two charts rather than two axes on one. A model keeps its
 * colour while the page is open, whatever the period; past MAX_MODELS the smallest are
 * "Other models". Colours are the --m-chart tokens of components.css.
 */
(function (root) {
    'use strict';

    const MAX_MODELS = 6;
    const OTHER = 'Other models';
    const HOUR = 3600 * 1000;

    const pad = (n) => String(n).padStart(2, '0');
    const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const hourKey = (d) => `${dayKey(d)}T${pad(d.getHours())}`;

    function hourly(hours) {
        return hours <= 48;
    }

    /** The columns from `hours` ago to now, without gaps: [{key, label, title}]. */
    function columns(hours, now) {
        const list = [];
        if (hourly(hours)) {
            const end = new Date(now);
            end.setMinutes(0, 0, 0);
            for (let i = hours; i >= 0; i -= 1) {
                const d = new Date(end.getTime() - i * HOUR);
                list.push({ key: hourKey(d), label: `${pad(d.getHours())}:00`, title: `${dayKey(d)} ${pad(d.getHours())}:00` });
            }
            return list;
        }
        // Day by day in local time: adding 24 hours would slip when the clocks change
        const first = new Date(now.getTime() - hours * HOUR);
        const d = new Date(first.getFullYear(), first.getMonth(), first.getDate());
        while (d <= now) {
            list.push({ key: dayKey(d), label: d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }), title: dayKey(d) });
            d.setDate(d.getDate() + 1);
        }
        return list;
    }

    /**
     * {columns, models, tokens: {model: [per column]}, cost: {...}, hasCost, missingCost}
     * of the API's by_hour rows. Models come by name; the smallest past MAX_MODELS are
     * folded into OTHER, which comes last, with those hasColor(model) has none for.
     */
    function series(byHour, hours, now, hasColor) {
        const cols = columns(hours, now);
        const at = new Map(cols.map((c, i) => [c.key, i]));
        const key = hourly(hours) ? hourKey : dayKey;
        const totals = new Map();
        const rows = (byHour || []).map((r) => ({
            model: r.model_name || 'unknown model',
            tokens: (r.prompt_tokens || 0) + (r.response_tokens || 0),
            cost: r.cost_usd,
            column: at.get(key(new Date(`${r.date}T${pad(r.hour)}:00:00Z`))),
        // A call just outside the columns (the clock moved on, or differs): the nearest
        })).map((r) => Object.assign(r, { column: r.column === undefined ? 0 : r.column }));
        rows.forEach((r) => totals.set(r.model, (totals.get(r.model) || 0) + r.tokens));

        // The largest first: they are the ones to get a colour
        let names = Array.from(totals.keys()).sort((a, b) => totals.get(b) - totals.get(a) || a.localeCompare(b));
        if (names.length > MAX_MODELS) names = names.slice(0, MAX_MODELS - 1);
        if (hasColor) names = names.filter(hasColor);
        const kept = new Set(names);
        const models = names.sort((a, b) => a.localeCompare(b));
        if (totals.size > kept.size) models.push(OTHER);
        const empty = () => cols.map(() => 0);
        const result = { columns: cols, models, tokens: {}, cost: {}, hasCost: false, missingCost: false };
        models.forEach((m) => { result.tokens[m] = empty(); result.cost[m] = empty(); });
        rows.forEach((r) => {
            const model = kept.has(r.model) ? r.model : OTHER;
            result.tokens[model][r.column] += r.tokens;
            if (r.cost === null || r.cost === undefined) result.missingCost = result.missingCost || r.tokens > 0;
            else { result.cost[model][r.column] += r.cost; result.hasCost = true; }
        });
        return result;
    }

    /** Colour slots by model, given once and kept; -1 for OTHER and past the slots. */
    function slots() {
        const given = new Map();
        return (model) => {
            if (model === OTHER) return -1;
            if (!given.has(model)) given.set(model, given.size < MAX_MODELS ? given.size : -1);
            return given.get(model);
        };
    }

    /** The models by colour slot, OTHER last. */
    function inOrder(models, slotOf) {
        const place = (model) => (slotOf(model) < 0 ? MAX_MODELS : slotOf(model));
        return models.slice().sort((a, b) => place(a) - place(b));
    }

    const compact = (n) => new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n);
    const money = (n) => `$${n === 0 ? '0' : n >= 1 ? n.toFixed(2) : n >= 0.01 ? n.toFixed(3) : n.toFixed(4)}`;
    const whole = (n) => Math.round(n).toLocaleString();

    function create(els) {
        const slotOf = slots();
        const charts = {};
        let last = null;

        const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        const colorName = (model) => {
            const slot = slotOf(model);
            return slot < 0 ? '--m-chart-other' : `--m-chart-${slot + 1}`;
        };

        function render(name, canvas, data, values, format, tick) {
            if (charts[name]) { charts[name].destroy(); delete charts[name]; }
            const total = data.models.reduce((sum, m) => sum + values[m].reduce((a, b) => a + b, 0), 0);
            canvas.setAttribute('aria-label', `${canvas.dataset.title}: ${format(total)} in all, `
                + data.models.map((m) => `${m} ${format(values[m].reduce((a, b) => a + b, 0))}`).join(', '));
            const surface = token('--bg-surface');
            const ink = token('--text-secondary');
            charts[name] = new root.Chart(canvas, {
                type: 'bar',
                data: {
                    labels: data.columns.map((c) => c.label),
                    datasets: data.models.map((model) => ({
                        label: model,
                        data: values[model],
                        backgroundColor: token(colorName(model)),
                        // A gap of the surface between the segments of a column
                        borderColor: surface,
                        borderWidth: { top: 2 },
                        // Rounded at the end of the whole column only
                        borderRadius: 4,
                        maxBarThickness: 24,
                    })),
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: 'index', intersect: false },
                    scales: {
                        x: { stacked: true, grid: { display: false }, border: { color: token('--border') },
                             ticks: { color: ink, maxRotation: 0, autoSkipPadding: 14 } },
                        y: { stacked: true, beginAtZero: true, grid: { color: token('--border') }, border: { display: false },
                             ticks: { color: ink, maxTicksLimit: 5, callback: tick } },
                    },
                    plugins: {
                        // The models' cards above are the legend
                        legend: { display: false },
                        tooltip: {
                            filter: (item) => item.raw > 0,
                            itemSort: (a, b) => b.raw - a.raw,
                            callbacks: {
                                title: (items) => (items.length ? data.columns[items[0].dataIndex].title : ''),
                                label: (item) => ` ${item.dataset.label}: ${format(item.raw)}`,
                                footer: (items) => (items.length > 1 ? `Total: ${format(items.reduce((s, i) => s + i.raw, 0))}` : ''),
                            },
                        },
                    },
                },
            });
        }

        function table(data) {
            if (!els.table) return;
            const cell = (tag, text, className) => {
                const el = document.createElement(tag);
                el.textContent = text;
                if (className) el.className = className;
                return el;
            };
            const row = (cells) => { const tr = document.createElement('tr'); tr.append(...cells); return tr; };
            const heads = ['When', ...data.models.map((m) => `${m}, tokens`), 'Tokens', 'Cost'];
            const head = document.createElement('thead');
            head.append(row(heads.map((h, i) => {
                const th = cell('th', h, i ? 'is-right' : '');
                th.scope = 'col';
                return th;
            })));
            const body = document.createElement('tbody');
            data.columns.forEach((column, i) => {
                const tokens = data.models.map((m) => data.tokens[m][i]);
                const cost = data.models.reduce((sum, m) => sum + data.cost[m][i], 0);
                const all = tokens.reduce((a, b) => a + b, 0);
                if (!all && !cost) return;   // the quiet ones are on the chart
                body.append(row([cell('td', column.title, 'm-cell-strong'), ...tokens.map((n) => cell('td', whole(n), 'is-right')),
                                 cell('td', whole(all), 'is-right'), cell('td', cost ? money(cost) : '—', 'is-right')]));
            });
            els.table.replaceChildren(head, body);
        }

        function paint() {
            if (!last || !root.Chart) return;
            render('tokens', els.tokens, last, last.tokens, whole, compact);
            if (last.hasCost) render('cost', els.cost, last, last.cost, money, money);
        }

        // The colours are read when drawn: draw again when the theme changes
        new MutationObserver(paint).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

        return {
            OTHER,
            /** Draw the rows; what was drawn, so the page can hide what has nothing. */
            draw(byHour, hours, now) {
                last = series(byHour, hours, now || new Date(), (model) => slotOf(model) >= 0);
                // Stacked in the colours' order, which is what keeps neighbours apart
                last.models = inOrder(last.models, slotOf);
                table(last);
                return last;
            },
            paint,
            colorOf: (model) => `var(${colorName(model)})`,
        };
    }

    const api = { create, series, columns, slots, inOrder, MAX_MODELS, OTHER };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.mateUsageChart = api;
})(typeof window !== 'undefined' ? window : globalThis);
