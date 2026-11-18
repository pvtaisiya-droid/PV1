document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-contact-form]').forEach(function (form) {
        var status = form.querySelector('[data-contact-status]');
        var participation = form.querySelector('[data-reconciliation-participation]');
        var warning = form.querySelector('[data-reconciliation-warning]');
        if (!status || !participation) {
            return;
        }
        var syncParticipation = function () {
            var inactive = status.value === 'inactive';
            if (inactive) {
                participation.value = 'none';
            }
            if (warning) {
                warning.classList.toggle('d-none', !inactive);
            }
        };
        status.addEventListener('change', syncParticipation);
        syncParticipation();

        var checkingDuplicates = false;
        var duplicateWarning;
        form.addEventListener('input', function (event) {
            if (event.target.name === 'allow_duplicate_contact') return;
            delete form.dataset.duplicatesChecked;
            var override = form.querySelector('[name="allow_duplicate_contact"]');
            if (override) override.checked = false;
        });
        form.addEventListener('change', function (event) {
            if (event.target.name !== 'allow_duplicate_contact') {
                delete form.dataset.duplicatesChecked;
                var override = form.querySelector('[name="allow_duplicate_contact"]');
                if (override) override.checked = false;
            }
        });
        form.addEventListener('submit', async function (event) {
            if (form.dataset.duplicatesChecked === 'true') return;
            event.preventDefault(); event.stopPropagation();
            if (checkingDuplicates) return;
            checkingDuplicates = true;
            var blocked = false;
            try {
                var response = await fetch('/contract-contacts/check-duplicates', {
                    method: 'POST', body: new FormData(form), credentials: 'same-origin'
                });
                if (!response.ok) throw new Error('Duplicate check unavailable');
                var result = await response.json();
                var previousOverride = form.querySelector('[name="allow_duplicate_contact"]');
                var approved = previousOverride && previousOverride.checked;
                if (result.contacts.length && (!approved || !result.can_create_separate)) {
                    if (!duplicateWarning) {
                        duplicateWarning = document.createElement('div');
                        duplicateWarning.className = 'col-12 alert alert-warning';
                        duplicateWarning.setAttribute('role', 'alert');
                        form.prepend(duplicateWarning);
                    }
                    duplicateWarning.replaceChildren();
                    var title = document.createElement('strong');
                    title.textContent = 'Проверьте возможные дубли контакта';
                    duplicateWarning.append(title);
                    result.contacts.forEach(function (contact) {
                        var line = document.createElement('div');
                        var link = document.createElement('a');
                        link.href = '/contract-contacts/' + encodeURIComponent(contact.id);
                        link.target = '_blank'; link.rel = 'noopener'; link.textContent = contact.name;
                        line.append(link); duplicateWarning.append(line);
                    });
                    if (result.can_create_separate) {
                        var label = document.createElement('label'); label.className = 'form-check mt-2';
                        var input = document.createElement('input'); input.type = 'checkbox';
                        input.className = 'form-check-input'; input.name = 'allow_duplicate_contact'; input.value = 'true';
                        var text = document.createElement('span'); text.className = 'form-check-label';
                        text.textContent = 'Проверено: это другой контакт, сохранить отдельную запись';
                        label.append(input, text); duplicateWarning.append(label);
                    } else {
                        var explanation = document.createElement('div');
                        explanation.textContent = 'У выбранного партнёра уже есть контакт с этим email. Используйте существующую запись или уточните email.';
                        duplicateWarning.append(explanation);
                    }
                    blocked = true;
                }
            } catch (_) {
                // Saving also performs the authoritative duplicate check.
            } finally {
                checkingDuplicates = false;
            }
            if (!blocked) {
                form.dataset.duplicatesChecked = 'true';
                form.requestSubmit(event.submitter);
            }
        });
    });
});
