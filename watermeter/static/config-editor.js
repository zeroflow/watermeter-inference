let editor = null;
let originalContent = '';
let hasUnsavedChanges = false;

// Configure Monaco loader
require.config({
    paths: {
        'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs'
    }
});

// Load Monaco and initialize
require(['vs/editor/editor.main'], function() {
    initEditor();
});

function initEditor() {
    // Register YAML language configuration
    monaco.languages.register({ id: 'yaml' });

    // Determine theme based on current data-theme
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const isDark = currentTheme === 'dark' ||
                  (currentTheme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    const monacoTheme = isDark ? 'vs-dark' : 'vs';

    // Create editor
    editor = monaco.editor.create(document.getElementById('editor'), {
        value: '# Loading config...',
        language: 'yaml',
        theme: monacoTheme,
        automaticLayout: true,
        minimap: { enabled: true },
        fontSize: 14,
        lineNumbers: 'on',
        renderWhitespace: 'selection',
        scrollBeyondLastLine: false,
        wordWrap: 'on',
        tabSize: 2,
        insertSpaces: true,
        formatOnPaste: false,  // Don't auto-format to preserve comments
        formatOnType: false,
    });

    // Listen for theme changes and update Monaco editor
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'data-theme') {
                const newTheme = document.documentElement.getAttribute('data-theme');
                const newIsDark = newTheme === 'dark' ||
                                 (newTheme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
                monaco.editor.setTheme(newIsDark ? 'vs-dark' : 'vs');
            }
        });
    });
    observer.observe(document.documentElement, { attributes: true });

    // Track changes
    editor.onDidChangeModelContent(() => {
        const currentContent = editor.getValue();
        hasUnsavedChanges = currentContent !== originalContent;
        updateStatus();
    });

    // Keyboard shortcuts
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        saveConfig();
    });

    // Hide loading overlay
    document.getElementById('loading-overlay').classList.add('hidden');

    // Load config and HA publish state
    loadConfig();
    loadHaPublishState();
}

function updateStatus() {
    const statusEl = document.getElementById('editor-status');
    const saveBtn = document.getElementById('save-btn');

    if (hasUnsavedChanges) {
        statusEl.textContent = 'Unsaved changes';
        statusEl.className = 'editor-status unsaved';
        saveBtn.disabled = false;
    } else {
        statusEl.textContent = 'Saved';
        statusEl.className = 'editor-status saved';
        saveBtn.disabled = true;
    }
}

async function loadConfig() {
    try {
        const statusEl = document.getElementById('editor-status');
        statusEl.textContent = 'Loading...';
        statusEl.className = 'editor-status';

        const response = await fetch('/api/config');
        const data = await response.json();

        if (data.success) {
            originalContent = data.content;
            editor.setValue(data.content);
            hasUnsavedChanges = false;
            updateStatus();
        } else {
            showMessage('Error loading config: ' + data.message, 'error');
            statusEl.textContent = 'Error loading';
            statusEl.className = 'editor-status error';
        }
    } catch (error) {
        console.error('Error loading config:', error);
        showMessage('Error loading config: ' + error.message, 'error');
    }
}

async function saveConfig() {
    if (!hasUnsavedChanges) return;

    const saveBtn = document.getElementById('save-btn');
    const statusEl = document.getElementById('editor-status');

    try {
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';
        saveBtn.classList.add('saving');
        statusEl.textContent = 'Saving...';
        statusEl.className = 'editor-status';

        const content = editor.getValue();

        const response = await fetch('/api/config/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                content: content,
                save_option: 'saveonly'
            })
        });

        const data = await response.json();

        if (data.success) {
            originalContent = content;
            hasUnsavedChanges = false;
            updateStatus();
            showMessage(data.message, 'success');
        } else {
            showMessage('Error: ' + data.message, 'error');
            statusEl.textContent = 'Save failed';
            statusEl.className = 'editor-status error';
        }
    } catch (error) {
        console.error('Error saving config:', error);
        showMessage('Error saving config: ' + error.message, 'error');
        statusEl.textContent = 'Save failed';
        statusEl.className = 'editor-status error';
    } finally {
        saveBtn.textContent = 'Save Config';
        saveBtn.classList.remove('saving');
        if (hasUnsavedChanges) {
            saveBtn.disabled = false;
        }
    }
}

function showMessage(text, type) {
    const toast = document.getElementById('message-toast');
    toast.textContent = text;
    toast.className = 'message-toast ' + type;

    // Auto-hide after 4 seconds
    setTimeout(() => {
        toast.className = 'message-toast';
    }, 4000);
}

function toggleHaPublish() {
    const checkbox = document.getElementById('ha-publish-toggle');
    const enabled = checkbox.checked;

    fetch('/api/toggle-ha-publish?enabled=' + enabled, {
        method: 'POST'
    })
    .then(response => {
        if (!response.ok) throw new Error(`Status ${response.status}`);
        return response.json();
    })
    .then(data => {
        showMessage(data.message || (enabled ? 'HA publishing enabled' : 'HA publishing disabled'), 'success');
    })
    .catch(error => {
        console.error('Error toggling HA publish:', error);
        showMessage('Error toggling HA publish: ' + error.message, 'error');
        // Revert checkbox state on error
        checkbox.checked = !enabled;
    });
}

async function loadHaPublishState() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error(`Status ${response.status}`);
        const data = await response.json();

        const checkbox = document.getElementById('ha-publish-toggle');
        if (checkbox && data.ha_publish_enabled !== undefined) {
            checkbox.checked = data.ha_publish_enabled;
        }
    } catch (error) {
        console.error('Error loading HA publish status:', error);
    }
}

// Warn before leaving with unsaved changes
window.addEventListener('beforeunload', (e) => {
    if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
    }
});
