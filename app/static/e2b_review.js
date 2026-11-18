document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('pv-package-review');
    if (!root) return;
    const all = selector => Array.from(root.querySelectorAll(selector));
    const one = selector => root.querySelector(selector);
    const steps = all('[data-review-step]');
    const params = new URL(location.href).searchParams;
    let current = steps.some(button => button.dataset.reviewStep === params.get('case')) ? params.get('case') : 'package';
    const pages = new Map();
    const dirtyFrames = new Set();
    if (current !== 'package' && ['overview', 'checks', 'intake'].includes(params.get('page'))) pages.set(current, params.get('page'));
    const urlState = () => {
        const url = new URL(location.href);
        url.searchParams.delete('message'); url.searchParams.delete('validation'); url.searchParams.delete('error');
        url.searchParams.set('case', current);
        url.searchParams.set('page', pages.get(current) || 'overview');
        history.replaceState(null, '', url);
    };
    const render = () => {
        steps.forEach(button => {
            const active = button.dataset.reviewStep === current;
            button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
        });
        all('[data-review-panel]').forEach(panel => { panel.hidden = panel.dataset.reviewPanel !== current; });
        const panel = all('[data-review-panel]').find(panel => panel.dataset.reviewPanel === current);
        if (current !== 'package') {
            const page = pages.get(current) || 'overview';
            panel.querySelectorAll('[data-case-tab]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.caseTab === page)));
            panel.querySelectorAll('[data-case-panel]').forEach(section => { section.hidden = section.dataset.casePanel !== page; });
            if (page === 'intake') {
                const frame = panel.querySelector('[data-intake-frame]');
                if (!frame.hasAttribute('src')) frame.src = frame.dataset.src;
            }
        }
        const index = steps.findIndex(button => button.dataset.reviewStep === current);
        one('[data-review-back]').disabled = index === 0;
        one('[data-review-next]').disabled = index === steps.length - 1;
        one('[data-review-progress]').textContent = `${index + 1} / ${steps.length}`;
        urlState();
    };
    steps.forEach((button, index) => {
        button.addEventListener('click', () => { current = button.dataset.reviewStep; render(); });
        button.addEventListener('keydown', event => {
            let next;
            if (event.key === 'ArrowRight') next = (index + 1) % steps.length;
            if (event.key === 'ArrowLeft') next = (index + steps.length - 1) % steps.length;
            if (event.key === 'Home') next = 0;
            if (event.key === 'End') next = steps.length - 1;
            if (next === undefined) return;
            event.preventDefault(); current = steps[next].dataset.reviewStep; render(); steps[next].focus();
        });
    });
    all('[data-open-case]').forEach(button => button.addEventListener('click', () => { current = button.dataset.openCase; render(); }));
    all('[data-case-tab]').forEach(button => button.addEventListener('click', () => {
        pages.set(button.closest('[data-review-panel]').dataset.reviewPanel, button.dataset.caseTab); render();
    }));
    all('[data-package-tab]').forEach(button => button.addEventListener('click', () => {
        all('[data-package-tab]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === button)));
        all('[data-package-panel]').forEach(panel => { panel.hidden = panel.dataset.packagePanel !== button.dataset.packageTab; });
    }));
    for (const [selector, offset] of [['[data-review-back]', -1], ['[data-review-next]', 1]]) {
        one(selector).addEventListener('click', () => {
            const index = steps.findIndex(button => button.dataset.reviewStep === current);
            current = steps[index + offset].dataset.reviewStep; render();
        });
    }
    const selected = all('[data-select-group]');
    const selectionForm = one('#package-import-selected');
    const documentChoices = document.createElement('input');
    documentChoices.type = 'hidden'; documentChoices.name = 'case_documents'; selectionForm.append(documentChoices);
    const updateDocuments = () => {
        documentChoices.value = JSON.stringify(Object.fromEntries(all('[data-case-group]').map(panel => [panel.dataset.caseGroup,
            Array.from(panel.querySelectorAll('[name="document_ids"]:checked')).map(input => input.value)])));
    };
    all('[data-case-group] [name="document_ids"]').forEach(input => input.addEventListener('change', updateDocuments));
    const updateSelection = () => {
        const count = selected.filter(input => input.checked && !input.disabled).length;
        one('[data-selected-count]').textContent = count;
        one('[data-import-selected]').disabled = !count;
    };
    selected.forEach(input => input.addEventListener('change', updateSelection));
    one('[data-select-ready]').addEventListener('click', () => { selected.forEach(input => { input.checked = !input.disabled; }); updateSelection(); });
    const rows = all('[data-file-row]');
    let filePage = 0;
    const filePages = Math.max(1, Math.ceil(rows.length / 6));
    const showFiles = () => {
        rows.forEach((row, index) => { row.hidden = Math.floor(index / 6) !== filePage; });
        one('[data-file-prev]').disabled = filePage === 0;
        one('[data-file-next]').disabled = filePage === filePages - 1;
        one('[data-file-page]').textContent = `${filePage + 1} / ${filePages}`;
    };
    one('[data-file-prev]').addEventListener('click', () => { filePage--; showFiles(); });
    one('[data-file-next]').addEventListener('click', () => { filePage++; showFiles(); });
    all('[data-intake-frame]').forEach(frame => {
        let wasDraft = false;
        let observer;
        frame.addEventListener('load', () => {
            const doc = frame.contentDocument;
            const form = doc?.querySelector('[data-message-intake]');
            if (!form) return;
            if (wasDraft && form.dataset.e2bDraft !== 'true') {
                dirtyFrames.delete(frame); current = frame.dataset.group; pages.set(current, 'intake'); urlState(); location.reload(); return;
            }
            wasDraft = form.dataset.e2bDraft === 'true';
            if (wasDraft) {
                const mark = event => { if (event.target === form || event.target.matches('input,textarea,select')) dirtyFrames.add(frame); };
                form.addEventListener('input', mark); form.addEventListener('change', mark);
                const source = all('[data-case-group]').find(panel => panel.dataset.caseGroup === frame.dataset.group);
                form.querySelectorAll('[name="document_ids"]').forEach(input => {
                    const original = Array.from(source.querySelectorAll('[name="document_ids"]')).find(other => other.value === input.value);
                    if (!original) return;
                    // A validation response retains the submitted document choices.
                    if (!doc.querySelector('[role="alert"]')) input.checked = original.checked;
                    original.addEventListener('change', () => { input.checked = original.checked; });
                    input.addEventListener('change', () => { original.checked = input.checked; updateDocuments(); });
                });
            }
            observer?.disconnect();
            observer = new ResizeObserver(() => {
                const height = Math.max(850, doc.body.scrollHeight + 24);
                if (Math.abs(frame.clientHeight - height) > 3) frame.style.height = height + 'px';
            });
            observer.observe(doc.body);
        });
    });
    root.addEventListener('submit', event => {
        if (!dirtyFrames.size) return;
        event.preventDefault();
        const frame = dirtyFrames.values().next().value;
        current = frame.dataset.group; pages.set(current, 'intake'); render();
        frame.contentDocument.querySelector('[data-intake-step-status]').textContent =
            document.documentElement.lang === 'ru' ? 'Сначала сохраните изменения этой формы кнопкой подтверждения импорта.' : 'Save this form using its import confirmation button first.';
    });
    window.addEventListener('beforeunload', event => { if (dirtyFrames.size) { event.preventDefault(); event.returnValue = ''; } });
    showFiles(); updateSelection(); updateDocuments(); render();
});
