/* eslint-disable */
/**
 * Monaco for the JSON fields of the agent forms: a field's "JSON" button swaps its
 * textarea for an editor (toggleJsonEditor). monacoEditors holds them by container
 * id; a container "<field>Editor" belongs to the field "<field>".
 *
 * Loaded by the agent page, the Agents list and the Visual Builder.
 */

mateActions.allow('toggleJsonEditor');

let monacoEditors = {};

/**
 * Initialize Monaco Editor
 */
function initMonacoEditor(containerId, initialValue = '', language = 'json') {
    return new Promise((resolve) => {
        require.config({ paths: { 'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs' } });
        require(['vs/editor/editor.main'], function () {
            const editor = monaco.editor.create(document.getElementById(containerId), {
                value: initialValue,
                language: language,
                theme: document.documentElement.classList.contains('dark') ? 'vs-dark' : 'vs',
                automaticLayout: true,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                wordWrap: 'on',
                formatOnPaste: true,
                formatOnType: true,
                suggestOnTriggerCharacters: true,
                acceptSuggestionOnEnter: 'on',
                tabCompletion: 'on',
                wordBasedSuggestions: 'off',
                folding: true,
                lineNumbers: 'on',
                renderLineHighlight: 'line',
                selectOnLineNumbers: true,
                roundedSelection: false,
                readOnly: false,
                cursorStyle: 'line',
                fontSize: 14,
                lineHeight: 20
            });

            // Add JSON validation only for JSON language
            if (language === 'json') {
                monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
                    validate: true,
                    allowComments: false,
                    schemas: []
                });
            }

            resolve(editor);
        });
    });
}

/**
 * Get JSON from Monaco editor
 */
function getJsonFromEditor(editor) {
    if (!editor) return '';
    return editor.getValue();
}

/**
 * Set JSON in Monaco editor
 */
function setJsonInEditor(editor, value) {
    if (!editor) return;
    editor.setValue(value);
}

/**
 * Toggle between textarea and Monaco editor
 */
function toggleJsonEditor(textareaId, editorId, language = 'json') {
    const textarea = document.getElementById(textareaId);
    const editorContainer = document.getElementById(editorId);

    if (editorContainer.style.display === 'none') {
        // Show Monaco editor
        textarea.style.display = 'none';
        editorContainer.style.display = 'block';

        if (!monacoEditors[editorId]) {
            initMonacoEditor(editorId, textarea.value, language).then(editor => {
                monacoEditors[editorId] = editor;
            });
        }
    } else {
        // Show textarea
        editorContainer.style.display = 'none';
        textarea.style.display = 'block';

        if (monacoEditors[editorId]) {
            textarea.value = getJsonFromEditor(monacoEditors[editorId]);
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }
}
