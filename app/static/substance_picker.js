document.querySelectorAll('[data-substance-picker]').forEach((picker) => {
    const search = picker.querySelector('[data-substance-search]');
    const select = picker.querySelector('select[name="active_substance_id"]');
    const empty = picker.querySelector('[data-substance-no-results]');
    if (!search || !select) {
        return;
    }
    const normalize = (value) => value.normalize('NFKC').toLocaleLowerCase('ru').replaceAll('ё', 'е').trim();
    const options = Array.from(select.options, (option) => ({
        element: option.cloneNode(true),
        text: normalize(option.textContent),
        value: option.value,
    }));
    const validateSelection = () => {
        select.setCustomValidity(search.value.trim() && !select.value ? select.dataset.selectionMessage : '');
    };
    search.addEventListener('input', () => {
        const query = normalize(search.value);
        const previous = select.value;
        const matches = options.filter((option) => !option.value || option.text.includes(query));
        select.replaceChildren(...matches.map((option) => option.element.cloneNode(true)));
        select.value = matches.some((option) => option.value === previous) ? previous : '';
        if (empty) {
            empty.hidden = matches.some((option) => option.value);
        }
        validateSelection();
    });
    select.addEventListener('change', validateSelection);
    select.form?.addEventListener('reset', () => {
        window.setTimeout(() => {
            select.replaceChildren(...options.map((option) => option.element.cloneNode(true)));
            if (empty) {
                empty.hidden = true;
            }
            validateSelection();
        }, 0);
    });
    picker.querySelector('[data-substance-search-container]').hidden = false;
});
