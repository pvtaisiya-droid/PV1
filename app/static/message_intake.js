document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-message-registration]').forEach(function (registration) {
        const method = registration.querySelector('#intake-method-e2b');
        if (!method) return;
        const manual = registration.querySelector('[data-message-intake]');
        const e2b = registration.querySelector('[data-e2b-registration]');
        const showMethod = () => { manual.hidden = method.checked; e2b.hidden = !method.checked; };
        registration.querySelectorAll('[name="registration_method"]').forEach(input => input.addEventListener('change', showMethod));
        showMethod();
    });
    document.querySelectorAll('[data-message-intake]').forEach(function (form) {
        const field = name => form.elements.namedItem(name);
        const one = selector => form.querySelector(selector);
        const all = selector => Array.from(form.querySelectorAll(selector));
        const contacts = new Map(JSON.parse(one('[data-intake-contacts]').textContent).map(person => [person.id, person]));
        const order = ['quality', 'medical', 'pv', 'other'];
        const routeProducts = all('[data-intake-products]').filter(select => select.dataset.intakeProducts !== 'common');
        const routeLabels = {quality: 'Качество', other: 'Другое', medical: 'Медицинский запрос', pv: 'НЯ и особые ситуации'};
        const registered = form.dataset.registered === 'true';
        const packageDraft = form.dataset.e2bDraft === 'true';
        const reporterBlock = one('[data-reporter-fields]');
        const reporterHome = one('[data-reporter-home]');
        const reporterPVHome = one('[data-reporter-pv-home]');
        const manualMedical = Object.fromEntries(['name', 'email', 'phone'].map(key => [key, field('medical_person_source').value === 'manual' ? field('medical_' + key).value : '']));
        const initialContent = (field('original_text').value || field('raw_text').value).normalize('NFKC').trim();
        let step = registered ? form.dataset.currentStep : 'registration';
        let senderExpanded = field('sender_contact_id').value === '__new__';
        let checking = false;
        let liveTimer;
        let revision = 0;
        let duplicateRequest = 0;

        const selected = () => order.filter(key => one('[data-intake-route][value="' + key + '"]').checked);
        const syncValidation = () => {
            // readonly controls retain their values and stay in FormData while
            // excluding semantically inactive fields from native validation.
            const active = selected();
            let card={};try{card=JSON.parse(field('pv_card_json')?.value||'{}');}catch(_){}
            all('input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"]), textarea').forEach(input => {
                const panel = input.closest('[data-intake-panel]')?.dataset.intakePanel;
                const separateReporter = input.closest('[data-separate-reporter]');
                const inactive = separateReporter ? field('reporter_same_as_sender').checked || field('reporter_unknown').checked :
                    order.includes(panel) && !active.includes(panel);
                const linkedMedical = ['medical_name', 'medical_email', 'medical_phone'].includes(input.name) && field('medical_person_source').value !== 'manual';
                const role=input.name.startsWith('sender_')?'sender':input.name.startsWith('reporter_')?'primary':null;
                const linkedPatient=role&&card.contacts?.[role]?.mode==='patient';
                input.readOnly = inactive || linkedMedical || linkedPatient;
            });
        };
        const person = role => {
            if (role === 'reporter' && field('reporter_same_as_sender').checked) return person('sender');
            if (role === 'reporter' && field('reporter_unknown').checked) return {name: '', email: '', phone: ''};
            return Object.fromEntries(['name', 'email', 'phone'].map(key => [key, field(role + '_' + key).value]));
        };
        const showPeople = () => {
            const sender = person('sender');
            const selectedContact = contacts.has(field('sender_contact_id').value);
            one('[data-sender-summary]').textContent = selectedContact ? [sender.name, sender.email, sender.phone].filter(Boolean).join(' · ') : '';
            one('[data-edit-sender]').hidden = !selectedContact;
            one('[data-sender-fields]').hidden = !(senderExpanded || field('sender_contact_id').value === '__new__');
            const saveSender = one('[data-save-sender]');
            if (saveSender) saveSender.hidden = selectedContact;
            const same = field('reporter_same_as_sender').checked;
            const unknown = field('reporter_unknown').checked;
            one('[data-separate-reporter]').hidden = same || unknown;
            one('[data-reporter-summary]').textContent = same ? 'Тот же контакт: ' + ([sender.name, sender.email, sender.phone].filter(Boolean).join(' · ') || 'сведения пока не указаны') : unknown ? 'Первичный репортёр пока неизвестен.' : '';
            const source = field('medical_person_source').value;
            const linked = source === 'sender' || source === 'reporter';
            const requester = linked ? person(source) : manualMedical;
            ['name', 'email', 'phone'].forEach(key => {
                const input = field('medical_' + key);
                input.readOnly = linked;
                if (linked) input.value = requester[key];
            });
            one('[data-medical-person-note]').textContent = linked ?
                (Object.values(requester).some(Boolean) ? 'Подставлено из общих сведений. Можно выбрать другого обращающегося.' : 'Общие сведения пока не заполнены. Укажите их в шаге 1 или выберите «Другой обращающийся».') : 'Укажите известное имя и контакты обращающегося.';
            one('[data-medical-contact]').hidden = linked;
            syncValidation();
        };
        const showConditional = () => {
            one('[data-quality-other]').hidden = !all('[name="quality_complaints"]:checked').some(input => input.value === 'Другое');
            one('[data-quality-documents]').hidden = !one('[name="quality_materials"][value="yes"]').checked;
            if(one('[data-special-situations]'))one('[data-special-situations]').hidden = !field('pv_special').checked;
            if(one('[data-special-other]'))one('[data-special-other]').hidden = !all('[name="pv_special_types"]:checked').some(input => input.value === 'Другое');
            if(one('[data-seriousness-details]'))one('[data-seriousness-details]').hidden = field('pv_seriousness').value !== 'yes';
        };
        const inheritProducts = () => {
            const ids = Array.from(field('product_ids').selectedOptions).map(option => option.value);
            routeProducts.forEach(select => {
                const kind = select.dataset.intakeProducts;
                if (field(kind + '_products_overridden').value !== 'true') {
                    Array.from(select.options).forEach(option => { option.selected = ids.includes(option.value); });
                }
            });
        };
        const updateDue = () => {
            if (field('medical_due_manual').value !== 'true') {
                const date = new Date(field('received_date').value + 'T00:00:00Z');
                if (Number.isFinite(date.getTime())) {
                    date.setUTCDate(date.getUTCDate() + 30);
                    field('medical_due_date').value = date.toISOString().slice(0, 10);
                } else field('medical_due_date').value = '';
            }
        };
        const render = () => {
            const active = selected();
            if (!registered) step = 'registration';
            if (step !== 'registration' && step !== 'directions' && !active.includes(step)) step = 'directions';
            all('[data-intake-panel]').forEach(panel => { panel.hidden = panel.dataset.intakePanel !== step; });
            all('[data-intake-step]').forEach(button => {
                const key = button.dataset.intakeStep;
                if (order.includes(key)) {
                    button.hidden = !registered || !active.includes(key);
                    button.textContent = (active.indexOf(key) + 3) + '. ' + routeLabels[key];
                }
                button.classList.toggle('btn-primary', key === step);
                button.classList.toggle('btn-outline-primary', key !== step);
                if (key === step) button.setAttribute('aria-current', 'step');
                else button.removeAttribute('aria-current');
            });
            if(reporterPVHome)(step === 'pv' ? reporterPVHome : reporterHome).append(reporterBlock);
            form.dataset.activeStep=step;field('current_step').value=step;
            if(one('[data-pv-handoff]'))one('[data-pv-handoff]').hidden=step!=='pv';
            field('next_step').value = registered ? step : 'directions';
            const next = one('[data-save-next]');
            if (next) next.hidden = step !== 'pv' && step !== 'registration' && (step === 'directions' ? !active.length : active.indexOf(step) === active.length - 1);
            showPeople(); showConditional();
        };
        const go = destination => { step = destination; render(); };
        if (packageDraft) all('[data-intake-step]').forEach(button => button.addEventListener('click', () => go(button.dataset.intakeStep)));

        all('[data-intake-route]').forEach(input => input.addEventListener('change', render));
        all('[data-source-message]').forEach(link => link.addEventListener('click', event => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            form.requestSubmit(one('[data-intake-step="registration"]'));
        }));
        one('[data-go-pv]').addEventListener('click', () => {
            one('[data-intake-route][value="pv"]').checked = true;
            if (packageDraft) go('pv');
            else form.requestSubmit(one('[data-intake-step="pv"]'));
        });
        one('.quality-help-trigger').addEventListener('keydown', event => {
            if (event.key === 'Escape') event.currentTarget.blur();
        });
        one('[data-edit-sender]').addEventListener('click', () => { senderExpanded = !senderExpanded; showPeople(); });
        all('[data-person-select]').forEach(select => select.addEventListener('change', () => {
            const role = select.dataset.personSelect;
            const contact = contacts.get(select.value);
            if (contact) {
                ['name', 'email', 'phone'].forEach(key => { field(role + '_' + key).value = contact[key] || ''; });
                if (role === 'sender' && contact.partner_id && !field('partner_id').value) field('partner_id').value = contact.partner_id;
                if (role === 'sender') {
                    senderExpanded = false;
                    if (field('save_sender_contact')) field('save_sender_contact').checked = false;
                }
            } else {
                ['name', 'email', 'phone'].forEach(key => { field(role + '_' + key).value = ''; });
                if (role === 'sender') senderExpanded = select.value === '__new__';
            }
            showPeople();
        }));
        ['sender', 'reporter'].forEach(role => ['name', 'email', 'phone'].forEach(key => field(role + '_' + key).addEventListener('input', showPeople)));
        ['reporter_same_as_sender', 'reporter_unknown'].forEach(name => field(name).addEventListener('change', () => {
            if (field(name).checked) field(name === 'reporter_unknown' ? 'reporter_same_as_sender' : 'reporter_unknown').checked = false;
            showPeople();
        }));
        field('medical_person_source').addEventListener('change', () => {
            if (field('medical_person_source').value === 'manual') ['name', 'email', 'phone'].forEach(key => { field('medical_' + key).value = manualMedical[key]; });
            showPeople();
        });
        field('medical_contact_id').addEventListener('change', () => {
            const contact = contacts.get(field('medical_contact_id').value);
            if (contact) ['name', 'email', 'phone'].forEach(key => {
                manualMedical[key] = contact[key] || '';
                field('medical_' + key).value = manualMedical[key];
            });
            showPeople();
        });
        ['name', 'email', 'phone'].forEach(key => field('medical_' + key).addEventListener('input', () => { manualMedical[key] = field('medical_' + key).value; }));
        field('product_ids').addEventListener('change', inheritProducts);
        routeProducts.forEach(select => select.addEventListener('change', () => { field(select.dataset.intakeProducts + '_products_overridden').value = 'true'; }));
        field('received_date').addEventListener('change', updateDue);
        field('medical_due_date').addEventListener('input', () => { field('medical_due_manual').value = 'true'; });
        one('[data-reset-medical-due]').addEventListener('click', () => { field('medical_due_manual').value = 'false'; updateDue(); });
        ['pv_special', 'pv_seriousness'].forEach(name => field(name)?.addEventListener('change', render));
        all('[name="quality_complaints"], [name="quality_materials"], [name="pv_special_types"]').forEach(input => input.addEventListener('change', showConditional));
        all('input, select, textarea').forEach(input => input.addEventListener('invalid', () => {
            const panel = input.closest('[data-intake-panel]');
            if (panel) go(panel.dataset.intakePanel);
            input.closest('details')?.setAttribute('open', '');
            if (input.name.startsWith('sender_')) { senderExpanded = true; showPeople(); }
        }));

        const reasonLabels = {content: 'совпадает содержание', file: 'совпадает содержимое файла', date: 'совпадает дата', sender: 'совпадают данные передавшего', products: 'совпадают ЛП', email: 'совпадает email', phone: 'совпадает телефон', name_partner: 'совпадают имя и организация'};
        const renderMatches = (type, matches) => {
            const alert = one(type === 'messages' ? '[data-message-duplicates]' : '[data-contact-duplicates]');
            const list = one(type === 'messages' ? '[data-message-duplicate-list]' : '[data-contact-duplicate-list]');
            list.replaceChildren();
            alert.hidden = !matches.length;
            matches.forEach(item => {
                const line = document.createElement('div');
                line.className = 'mt-2';
                const link = document.createElement('a');
                link.href = type === 'messages' ? '/safety-reports/' + encodeURIComponent(item.id) + '/intake' : '/contract-contacts/' + encodeURIComponent(item.id);
                link.target = '_blank'; link.rel = 'noopener';
                link.textContent = item.number || item.label || item.name;
                line.append(link, document.createTextNode(' — ' + item.reasons.map(reason => reasonLabels[reason] || reason).join(', ')));
                if (type === 'contacts') {
                    contacts.set(item.id, item);
                    const button = document.createElement('button');
                    button.type = 'button'; button.className = 'btn btn-sm btn-outline-primary ms-2';
                    button.dataset.useContact = item.id; button.textContent = 'Выбрать этот контакт';
                    line.append(button);
                }
                list.append(line);
            });
        };
        form.addEventListener('click', event => {
            const button = event.target.closest('[data-use-contact]');
            if (!button) return;
            const id = button.dataset.useContact;
            const select = field('sender_contact_id');
            if (!Array.from(select.options).some(option => option.value === id)) {
                const option = document.createElement('option'); option.value = id;
                option.textContent = contacts.get(id)?.label || contacts.get(id)?.name || id; select.append(option);
            }
            select.value = id; select.dispatchEvent(new Event('change', {bubbles: true}));
            one('[data-contact-duplicates]').hidden = true;
        });
        const checkDuplicates = async withFiles => {
            const request = ++duplicateRequest;
            const currentRevision = revision;
            const body = new FormData();
            ['csrf', 'report_id', 'received_date', 'raw_text', 'original_text', 'sender_name', 'sender_email', 'sender_phone', 'partner_id'].forEach(name => body.append(name, field(name).value));
            const fileInputs = all('input[type="file"]');
            const files = fileInputs.flatMap(input => Array.from(input.files || []));
            const unchanged = registered && (field('original_text').value || field('raw_text').value).normalize('NFKC').trim() === initialContent && !files.length;
            if (unchanged) body.append('skip_messages', 'true');
            if (withFiles && window.crypto?.subtle) {
                for (const file of files) {
                    const hash = await window.crypto.subtle.digest('SHA-256', await file.arrayBuffer());
                    body.append('checksums', Array.from(new Uint8Array(hash)).map(byte => byte.toString(16).padStart(2, '0')).join(''));
                }
            }
            const response = await fetch('/safety-reports/check-duplicates', {method: 'POST', body, credentials: 'same-origin'});
            if (!response.ok) throw new Error('Duplicate check unavailable');
            const result = await response.json();
            if (currentRevision !== revision || request !== duplicateRequest) throw new Error('Duplicate check is stale');
            result.contacts = result.contacts.filter(item => item.id !== field('sender_contact_id').value);
            renderMatches('messages', result.messages); renderMatches('contacts', result.contacts);
            field('allow_duplicate_contact').closest('label').hidden = result.contact_separate_allowed === false;
            return result;
        };
        const relevant = name => ['raw_text', 'original_text', 'sender_contact_id', 'sender_name', 'sender_email', 'sender_phone', 'partner_id', 'received_date', 'save_sender_contact'].includes(name) || name.endsWith('_files');
        const invalidate = event => {
            revision += 1;
            syncValidation();
            if (!relevant(event.target.name)) return;
            delete form.dataset.duplicateChecked;
            field('allow_duplicate_message').checked = false;
            if (event.target.name.startsWith('sender_') || event.target.name === 'partner_id') field('allow_duplicate_contact').checked = false;
            clearTimeout(liveTimer);
            if (field('raw_text').value.trim() || field('sender_email').value || field('sender_phone').value || field('sender_name').value) {
                liveTimer = setTimeout(() => { if (!checking) checkDuplicates(false).catch(() => {}); }, 650);
            }
        };
        form.addEventListener('input', invalidate); form.addEventListener('change', invalidate);
        form.addEventListener('submit', async event => {
            const button = event.submitter;
            if (button?.value === 'handoff' && form.dataset.handoffConfirmed !== 'true') {
                if (!window.confirm('Сохранить и продолжить?')) {
                    event.preventDefault(); event.stopPropagation(); return;
                }
                form.dataset.handoffConfirmed = 'true';
            }
            const active = selected();
            if (button?.dataset.intakeStep) {
                field('next_step').value = button.dataset.intakeStep;
            } else if (button?.value === 'next') {
                const sequence = ['registration', 'directions', ...active];
                field('next_step').value = sequence[sequence.indexOf(step) + 1] || step;
            } else field('next_step').value = registered ? step : 'directions';
            if (form.dataset.duplicateChecked === 'true') return;
            event.preventDefault(); event.stopPropagation();
            if (checking) return;
            checking = true;
            clearTimeout(liveTimer);
            const currentRevision = revision;
            one('[data-intake-step-status]').textContent = 'Проверяем возможные дубли…';
            let blocked = false;
            try {
                const result = await checkDuplicates(true);
                blocked = (result.messages.length && !field('allow_duplicate_message').checked) ||
                    (result.contacts.length && field('save_sender_contact')?.checked &&
                     (!field('allow_duplicate_contact').checked || result.contact_separate_allowed === false) && !contacts.has(field('sender_contact_id').value));
            } catch (_) {
                // The authoritative check still runs inside the saving transaction.
            } finally {
                checking = false;
            }
            one('[data-intake-step-status]').textContent = blocked ? 'Проверьте найденные совпадения перед сохранением.' : '';
            if (currentRevision !== revision) {
                delete form.dataset.handoffConfirmed;
                one('[data-intake-step-status]').textContent = 'Сведения изменились во время проверки. Сохраните ещё раз.';
                return;
            }
            if (blocked) delete form.dataset.handoffConfirmed;
            if (!blocked) {
                form.dataset.duplicateChecked = 'true';
                form.requestSubmit(button);
            }
        });
        inheritProducts(); updateDue(); render();
    });
});
