/**
 * The blocks of an agent's message: paragraphs, lists and tables. Used by the Work Room
 * and standalone chat (standalone/chat.js) and the widget (widget/chat.js); it is in
 * standalone/ because a standalone build copies that folder.
 *
 *   mateChatMarkdown.blocks(escapedText, inline)
 *
 * The text must be HTML-escaped already; inline(text) turns the Markdown within a line
 * (bold, links, code) into HTML. Only the tags written here are added around it.
 */
(function (root) {
    'use strict';

    const ITEM = /^(\s*)(?:[*+-]|(\d{1,9})[.)])\s+(\S.*)$/;
    const DEEPEST = 8;  // lists within lists; the text is untrusted
    const RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

    function cells(line) {
        // \u0001 stands for an escaped bar. No lookbehind: Safari before 16.4 can't parse it
        const row = line.replace(/\u0001/g, '').replace(/\\\|/g, '\u0001').trim();
        return row.replace(/^\|/, '').replace(/\|$/, '').split('|')
            .map((cell) => cell.trim().replace(/\u0001/g, '|'));
    }

    function tableStarts(lines, i) {
        return i + 1 < lines.length && lines[i].includes('|') && lines[i + 1].includes('|')
            && RULE.test(lines[i + 1]) && cells(lines[i]).length === cells(lines[i + 1]).length;
    }

    function table(lines, i, inline) {
        const head = cells(lines[i]);
        const align = cells(lines[i + 1]).map((c) => (
            /^:.*:$/.test(c) ? 'center' : c.endsWith(':') ? 'right' : ''));
        const tag = (name, cell, n) => `<${name}${align[n] ? ` class="md-${align[n]}"` : ''}>${inline(cell)}</${name}>`;
        const rows = [];
        let end = i + 2;
        for (; end < lines.length && lines[end].trim() && lines[end].includes('|'); end += 1) {
            const row = cells(lines[end]);
            rows.push(`<tr>${head.map((_, n) => tag('td', row[n] || '', n)).join('')}</tr>`);
        }
        // An empty header row is how a model writes a table without one
        const heads = head.some(Boolean) ? `<thead><tr>${head.map((c, n) => tag('th', c, n)).join('')}</tr></thead>` : '';
        return [`<div class="md-table"><table>${heads}<tbody>${rows.join('')}</tbody></table></div>`, end];
    }

    // The list whose first item is line i: items as indented as it, with deeper ones in
    // the item before them. Blank lines may stand between items.
    function list(lines, i, inline, level) {
        const [, indent, number] = lines[i].match(ITEM);
        const depth = indent.length;
        const items = [];
        let end = i;
        while (end < lines.length) {
            let next = end;
            while (next < lines.length && !lines[next].trim()) next += 1;
            const found = next < lines.length && lines[next].match(ITEM);
            const deeper = Boolean(found) && found[1].length > depth;
            const sameKind = Boolean(found) && Boolean(found[2]) === Boolean(number);
            if (sameKind && (found[1].length === depth || (deeper && level >= DEEPEST))) {
                items.push(inline(found[3]));
                end = next + 1;
            } else if (deeper && level < DEEPEST) {
                const [html, after] = list(lines, next, inline, level + 1);
                items[items.length - 1] += html;
                end = after;
            } else if (!found && next === end && end < lines.length && /^\s{2,}\S/.test(lines[end])) {
                items[items.length - 1] += `<br>${inline(lines[end].trim())}`;  // the item goes on
                end += 1;
            } else break;
        }
        const tag = number ? 'ol' : 'ul';
        const start = number && Number(number) !== 1 ? ` start="${Number(number)}"` : '';
        return [`<${tag}${start}>${items.map((item) => `<li>${item}</li>`).join('')}</${tag}>`, end];
    }

    function blocks(text, inline) {
        const lines = String(text).split(/\r?\n/);
        const out = [];
        let paragraph = [];
        const flush = () => {
            if (paragraph.length) out.push(`<p>${inline(paragraph.join('\n')).replace(/\n/g, '<br>')}</p>`);
            paragraph = [];
        };
        for (let i = 0; i < lines.length;) {
            const build = ITEM.test(lines[i]) ? list : tableStarts(lines, i) ? table : null;
            if (build) {
                flush();
                const [html, end] = build(lines, i, inline, 1);
                out.push(html);
                i = end;
                continue;
            }
            if (lines[i].trim()) paragraph.push(lines[i]);
            else flush();
            i += 1;
        }
        flush();
        return out.join('');
    }

    const api = { blocks };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.mateChatMarkdown = api;
})(typeof window !== 'undefined' ? window : globalThis);
