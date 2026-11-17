// Carry the version of the option shown to the user, never fetch a new token
// while submitting an old form.
document.querySelectorAll('select[data-version-field]').forEach(select => {
    const update = () => {
        const option = select.selectedOptions[0];
        let token = select.form.elements.namedItem(select.dataset.versionField);
        if (!token) {
            token = document.createElement('input');
            token.type = 'hidden';
            token.name = select.dataset.versionField;
            select.form.appendChild(token);
        }
        token.value = option?.dataset.version || '';
        if (select.dataset.typeField) {
            select.form.elements.namedItem(select.dataset.typeField).value = option?.dataset.type || '';
        }
    };
    select.addEventListener('change', update);
    update();
});
