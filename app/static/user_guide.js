(function () {
    "use strict";

    const main = document.querySelector("main.app-content");
    const openButton = document.querySelector("[data-page-guide-open]");
    if (!main || !openButton || !window.bootstrap) {
        return;
    }

    const language = document.body.dataset.lang === "en" ? "en" : "ru";
    const pageKey = document.body.dataset.page || "dashboard";
    const text = {
        ru: {
            help: "Помощь по странице",
            guideTitle: "Как работать на этой странице",
            close: "Закрыть",
            start: "Показать шаги на экране",
            restart: "Начать сначала",
            summaryPrefix: "Порядок работы:",
            show: "Показать",
            step: "Шаг",
            of: "из",
            back: "Назад",
            next: "Далее",
            done: "Готово",
            exit: "Завершить обучение",
            overviewTitle: "Сначала сориентируйтесь",
            overviewBody: "Проверьте название страницы и краткую сводку. Так вы убедитесь, что работаете в нужном разделе.",
            tabsTitle: "Выберите рабочий раздел",
            tabsBody: "Переключайтесь между вкладками по этапам задачи. Данные сохраняются только после нажатия кнопки сохранения внутри выбранной вкладки.",
            filtersTitle: "Найдите нужные записи",
            filtersBody: "Задайте один или несколько фильтров и нажмите кнопку поиска. Чтобы снова увидеть все записи, очистите фильтры.",
            formTitle: "Заполните рабочую форму",
            formBody: "Заполните обязательные поля, проверьте значения и только затем нажмите основную кнопку действия в этой форме.",
            dataTitle: "Проверьте результат",
            dataBody: "Просмотрите найденные записи и их статусы. Для продолжения откройте нужную строку или используйте действие рядом с ней.",
            actionsTitle: "Завершите текущий этап",
            actionsBody: "Используйте основные кнопки этого блока после проверки данных. Опасные действия запрашивают отдельное подтверждение.",
            defaultPurpose: "Просмотрите данные страницы, выполните нужное действие и проверьте результат.",
            noSteps: "На этой странице пока нет доступных действий. Используйте описание раздела или вернитесь в главное меню.",
            reconChooseTitle: "Выберите партнёра и период",
            reconChooseBody: "Укажите партнёра, контакт и даты сверки. Для быстрого выбора периода можно нажать готовый вариант, затем проверьте заполненные даты.",
            reconGenerateTitle: "Сформируйте сверку",
            reconGenerateBody: "Нажмите основную кнопку в этом блоке. Система сопоставит сообщения партнёра с внутренними кейсами за выбранный период.",
            reconReviewTitle: "Проверьте результат и расхождения",
            reconReviewBody: "Сравните количества и строки с расхождениями. Для спорных записей укажите связь с внутренним кейсом и сохраните решение.",
            reconFinishTitle: "Зафиксируйте результат",
            reconFinishBody: "После проверки обновите статус, сформируйте файл или сохраните ответ партнёра. Завершайте сверку только когда все расхождения разобраны."
        },
        en: {
            help: "Page help",
            guideTitle: "How to use this page",
            close: "Close",
            start: "Show steps on screen",
            restart: "Start again",
            summaryPrefix: "Suggested workflow:",
            show: "Show",
            step: "Step",
            of: "of",
            back: "Back",
            next: "Next",
            done: "Done",
            exit: "End guide",
            overviewTitle: "Start with the page context",
            overviewBody: "Check the page title and summary to make sure you are working in the correct section.",
            tabsTitle: "Choose a working section",
            tabsBody: "Move between tabs as the task progresses. Data is saved only after you use the save action inside the selected tab.",
            filtersTitle: "Find the records you need",
            filtersBody: "Set one or more filters and run the search. Clear the filters to return to the complete list.",
            formTitle: "Complete the working form",
            formBody: "Fill in required fields, verify the values, and then use the main action in this form.",
            dataTitle: "Check the result",
            dataBody: "Review the matching records and statuses. Open the relevant row or use its adjacent action to continue.",
            actionsTitle: "Complete this stage",
            actionsBody: "Use the primary actions in this area after checking the data. Destructive actions ask for separate confirmation.",
            defaultPurpose: "Review the page data, complete the required action, and check the result.",
            noSteps: "There are no available actions on this page yet. Use the section description or return to the main menu.",
            reconChooseTitle: "Select a partner and period",
            reconChooseBody: "Choose the partner, contact and reconciliation dates. You can use a period preset, then verify the populated dates.",
            reconGenerateTitle: "Generate the reconciliation",
            reconGenerateBody: "Use the primary action in this area. The system will match partner messages with internal cases for the selected period.",
            reconReviewTitle: "Review results and discrepancies",
            reconReviewBody: "Compare totals and discrepancy rows. Link uncertain records to internal cases and save each decision.",
            reconFinishTitle: "Record the result",
            reconFinishBody: "After review, update the status, generate the file, or save the partner response. Close the reconciliation only after resolving all discrepancies."
        }
    }[language];

    const purposes = {
        dashboard: {
            ru: "Начните с просроченных и ближайших задач, затем откройте объект, который требует внимания.",
            en: "Start with overdue and upcoming tasks, then open the item that needs attention."
        },
        partners: {
            ru: "Найдите или создайте партнёра, заполните его карточку, контакты и договоры, затем переходите к сверке.",
            en: "Find or create a partner, complete its profile, contacts and agreements, then proceed to reconciliation."
        },
        contract_contacts: {
            ru: "Сначала выберите партнёра, затем добавьте актуальное контактное лицо и укажите его роль в обмене данными.",
            en: "Select a partner first, then add a current contact and specify their data-exchange role."
        },
        contracts: {
            ru: "Найдите договор или создайте новый, привяжите партнёра и препараты, затем проверьте сроки и услуги.",
            en: "Find or create an agreement, link the partner and products, then verify dates and services."
        },
        contract_imports: {
            ru: "Загрузите документ, проверьте распознанные значения и совпадения, исправьте спорные поля и подтвердите импорт.",
            en: "Upload a document, review extracted values and matches, correct uncertain fields, and confirm the import."
        },
        accounting_data: {
            ru: "Выберите период и партнёра, проверьте начисления и статусы оплаты, затем откройте нужную запись.",
            en: "Select a period and partner, review charges and payment statuses, then open the required record."
        },
        products: {
            ru: "Найдите или добавьте препарат, затем заполните держателя, договоры, вещества и регистрационные данные.",
            en: "Find or add a product, then complete its holder, agreements, substances and registration data."
        },
        substances: {
            ru: "Создайте действующее вещество при необходимости и привяжите его к соответствующему препарату.",
            en: "Create an active substance when needed and link it to the appropriate product."
        },
        dosage_forms: {
            ru: "Найдите существующую лекарственную форму перед созданием новой, чтобы избежать дублей.",
            en: "Search existing dosage forms before creating a new one to avoid duplicates."
        },
        safety_reports: {
            ru: "Найдите входящее сообщение или загрузите E2B, выполните первичную проверку и создайте кейс только после подтверждения критериев.",
            en: "Find an incoming report or import E2B, complete triage, and create a case only after confirming the criteria."
        },
        incoming_requests: {
            ru: "Откройте входящее сообщение, проверьте классификацию и связи, затем назначьте дальнейший маршрут обработки.",
            en: "Open an incoming message, verify its classification and links, then assign the next processing route."
        },
        cases: {
            ru: "Найдите кейс, последовательно заполните данные, добавьте follow-up при необходимости и только затем готовьте отправку.",
            en: "Find a case, complete its data in sequence, add follow-up if needed, and only then prepare submission."
        },
        partner_reconciliation: {
            ru: "Шаг 1 — выберите партнёра и период. Шаг 2 — сформируйте сверку. Шаг 3 — проверьте расхождения и сохраните результат.",
            en: "Step 1: select a partner and period. Step 2: generate the reconciliation. Step 3: review discrepancies and save the result."
        },
        gpt_analysis: {
            ru: "Выберите сообщение, запустите анализ и обязательно проверьте предложенные системой выводы перед использованием.",
            en: "Select a message, run the analysis, and always review the suggested conclusions before using them."
        },
        literature_monitoring: {
            ru: "Задайте период и поисковый запрос, проверьте найденные публикации и зафиксируйте результат мониторинга.",
            en: "Set the period and search query, review the publications found, and record the monitoring result."
        },
        psmf: {
            ru: "Работайте по вкладкам: проверьте настройки, заполните главы и приложения, соберите документ, согласуйте и сформируйте финальную версию.",
            en: "Work through the tabs: verify settings, complete chapters and annexes, build the document, approve it, and create the final version."
        },
        psur: {
            ru: "Создайте план, добавьте препараты и кейсы, соберите ответы партнёров и разделы отчёта, затем проверьте документы и задачи.",
            en: "Create a plan, add products and cases, collect partner responses and report sections, then review documents and tasks."
        },
        sops: {
            ru: "Найдите действующую процедуру или добавьте новую версию, затем проверьте связанные файлы, обучение и историю.",
            en: "Find the active procedure or add a new version, then review linked files, training and history."
        },
        submissions: {
            ru: "Создайте отправку для готового кейса, укажите получателя и срок, затем контролируйте изменение статуса.",
            en: "Create a submission for a ready case, specify recipient and due date, then monitor its status."
        },
        tasks: {
            ru: "Сначала отфильтруйте задачи по сроку и статусу, затем откройте приоритетную задачу и обновите результат.",
            en: "Filter tasks by due date and status first, then open the priority task and update its result."
        },
        documents: {
            ru: "Найдите документы по связанному объекту, загрузите нужный файл и проверьте его категорию и доступность.",
            en: "Find documents by related item, upload the required file, and verify its category and availability."
        },
        users_roles: {
            ru: "Создайте или найдите пользователя, назначьте минимально необходимые роли и проверьте разрешения перед сохранением.",
            en: "Create or find a user, assign only the required roles, and review permissions before saving."
        },
        audit_log: {
            ru: "Отфильтруйте историю по объекту, пользователю или периоду, затем откройте нужное изменение для проверки.",
            en: "Filter history by item, user or period, then open the relevant change for review."
        }
    };

    function isVisible(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
    }

    function firstVisible(selectors, scope) {
        const searchRoot = scope || main;
        for (const selector of selectors) {
            const match = Array.from(searchRoot.querySelectorAll(selector)).find(isVisible);
            if (match) return match;
        }
        return null;
    }

    function enclosingBlock(element) {
        if (!element) return null;
        return element.closest("section, .surface, .card, .tab-content, .table-responsive") || element;
    }

    function distinctTarget(steps, target) {
        return target && !steps.some((step) => step.target === target);
    }

    function buildSteps() {
        const steps = [];
        if (pageKey === "partner_reconciliation") {
            const setup = enclosingBlock(firstVisible(["#reconciliation-main-form"]));
            const generate = firstVisible([".recon-action-bar"]);
            const result = firstVisible(["#reconciliation-result", ".recon-message-sections"]);
            const finish = firstVisible([".recon-tools-grid", ".recon-control-panel"]);
            if (setup) steps.push({ target: setup, title: text.reconChooseTitle, body: text.reconChooseBody });
            if (generate && distinctTarget(steps, generate)) steps.push({ target: generate, title: text.reconGenerateTitle, body: text.reconGenerateBody });
            if (result && distinctTarget(steps, result)) steps.push({ target: result, title: text.reconReviewTitle, body: text.reconReviewBody });
            if (finish && distinctTarget(steps, finish)) steps.push({ target: finish, title: text.reconFinishTitle, body: text.reconFinishBody });
            return steps;
        }

        const heading = firstVisible(["h1"]);
        if (heading) {
            steps.push({ target: heading.closest("header, .d-flex") || heading, title: text.overviewTitle, body: text.overviewBody });
        }

        const tabs = firstVisible([".nav-tabs", "[role='tablist']", ".pv-period-tabs", ".pv-onboarding-steps"]);
        if (distinctTarget(steps, tabs)) {
            steps.push({ target: tabs, title: text.tabsTitle, body: text.tabsBody });
        }

        const filterForm = firstVisible(["form[method='get']"]);
        const filterBlock = enclosingBlock(filterForm);
        if (distinctTarget(steps, filterBlock)) {
            steps.push({ target: filterBlock, title: text.filtersTitle, body: text.filtersBody });
        }

        const workingForms = Array.from(main.querySelectorAll("form[method='post']")).filter((form) => {
            if (!isVisible(form)) return false;
            const action = (form.getAttribute("action") || "").toLowerCase();
            return !action.includes("/delete") && !action.includes("/archive") && !action.includes("/status") && !form.closest("td");
        });
        const workingBlock = enclosingBlock(workingForms[0]);
        if (distinctTarget(steps, workingBlock)) {
            steps.push({ target: workingBlock, title: text.formTitle, body: text.formBody });
        }

        const dataArea = firstVisible([".pv-table-scroll", ".table-responsive", ".pv-card-grid", ".accordion", "table", ".list-group"]);
        const dataBlock = enclosingBlock(dataArea);
        if (distinctTarget(steps, dataBlock)) {
            steps.push({ target: dataBlock, title: text.dataTitle, body: text.dataBody });
        }

        const actionArea = firstVisible([".recon-action-bar", ".page-actions", ".pv-onboarding-actions", ".modal-footer"]);
        if (distinctTarget(steps, actionArea)) {
            steps.push({ target: actionArea, title: text.actionsTitle, body: text.actionsBody });
        }

        return steps.slice(0, 6);
    }

    const purposeEntry = purposes[pageKey];
    const purpose = purposeEntry ? purposeEntry[language] : text.defaultPurpose;
    let steps = buildSteps();
    let activeStep = -1;
    let previousFocus = null;

    const panel = document.createElement("div");
    panel.className = "offcanvas offcanvas-end pv-guide-panel";
    panel.tabIndex = -1;
    panel.id = "pageGuidePanel";
    panel.setAttribute("aria-labelledby", "pageGuideTitle");
    panel.innerHTML = `
        <div class="offcanvas-header pv-guide-panel-header">
            <div>
                <div class="pv-guide-kicker"></div>
                <h2 class="offcanvas-title h5 mb-0" id="pageGuideTitle"></h2>
            </div>
            <button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label=""></button>
        </div>
        <div class="offcanvas-body d-flex flex-column">
            <p class="pv-guide-purpose"></p>
            <ol class="pv-guide-list"></ol>
            <div class="pv-guide-empty alert alert-light border" hidden></div>
            <button class="btn btn-primary icon-button mt-auto pv-guide-start" type="button">
                <i data-lucide="route"></i><span></span>
            </button>
        </div>`;
    document.body.appendChild(panel);

    panel.querySelector(".pv-guide-kicker").textContent = text.help;
    panel.querySelector("#pageGuideTitle").textContent = text.guideTitle;
    panel.querySelector(".btn-close").setAttribute("aria-label", text.close);
    panel.querySelector(".pv-guide-purpose").textContent = purpose;
    panel.querySelector(".pv-guide-start span").textContent = text.start;
    panel.querySelector(".pv-guide-empty").textContent = text.noSteps;

    const panelController = window.bootstrap.Offcanvas.getOrCreateInstance(panel);

    function renderStepList() {
        const list = panel.querySelector(".pv-guide-list");
        list.replaceChildren();
        panel.querySelector(".pv-guide-empty").hidden = steps.length > 0;
        panel.querySelector(".pv-guide-start").hidden = steps.length === 0;
        steps.forEach((step, index) => {
            const item = document.createElement("li");
            item.className = "pv-guide-list-item";
            const content = document.createElement("div");
            const title = document.createElement("strong");
            const body = document.createElement("span");
            const button = document.createElement("button");
            title.textContent = `${index + 1}. ${step.title}`;
            body.textContent = step.body;
            button.className = "btn btn-link btn-sm p-0";
            button.type = "button";
            button.textContent = text.show;
            button.addEventListener("click", () => {
                panel.addEventListener("hidden.bs.offcanvas", () => showStep(index), { once: true });
                panelController.hide();
            });
            content.append(title, body);
            item.append(content, button);
            list.appendChild(item);
        });
    }

    renderStepList();

    const summary = document.createElement("aside");
    summary.className = "pv-page-guide-summary";
    summary.setAttribute("aria-label", text.help);
    const summaryIcon = document.createElement("i");
    summaryIcon.setAttribute("data-lucide", "route");
    const summaryText = document.createElement("span");
    const summaryPrefix = document.createElement("strong");
    summaryPrefix.textContent = text.summaryPrefix + " ";
    summaryText.append(summaryPrefix, document.createTextNode(purpose));
    const summaryButton = document.createElement("button");
    summaryButton.className = "btn btn-sm btn-outline-primary flex-shrink-0";
    summaryButton.type = "button";
    summaryButton.textContent = text.show;
    summaryButton.addEventListener("click", () => panelController.show());
    summary.append(summaryIcon, summaryText, summaryButton);

    const h1 = main.querySelector("h1");
    const pageHeader = h1 && (h1.closest("header") || h1.closest(".d-flex.mb-3") || h1.parentElement);
    const constrainedPageGrid = pageHeader && pageHeader.closest(".pv-home");
    if (constrainedPageGrid && constrainedPageGrid.parentElement) {
        constrainedPageGrid.insertAdjacentElement("beforebegin", summary);
    } else if (pageHeader && pageHeader.parentElement) {
        pageHeader.insertAdjacentElement("afterend", summary);
    } else {
        main.prepend(summary);
    }

    const backdrop = document.createElement("div");
    backdrop.className = "pv-guide-backdrop";
    backdrop.hidden = true;
    document.body.appendChild(backdrop);

    const popover = document.createElement("section");
    popover.className = "pv-guide-popover";
    popover.hidden = true;
    popover.setAttribute("role", "dialog");
    popover.setAttribute("aria-modal", "true");
    popover.innerHTML = `
        <div class="pv-guide-progress"></div>
        <h2 class="h6 mb-2 pv-guide-step-title"></h2>
        <p class="mb-3 pv-guide-step-body"></p>
        <div class="d-flex justify-content-between align-items-center gap-2">
            <button class="btn btn-link btn-sm px-0 pv-guide-exit" type="button"></button>
            <div class="d-flex gap-2">
                <button class="btn btn-outline-secondary btn-sm pv-guide-back" type="button"></button>
                <button class="btn btn-primary btn-sm pv-guide-next" type="button"></button>
            </div>
        </div>`;
    document.body.appendChild(popover);
    popover.querySelector(".pv-guide-exit").textContent = text.exit;
    popover.querySelector(".pv-guide-back").textContent = text.back;

    function placePopover(target) {
        const rect = target.getBoundingClientRect();
        const gap = 14;
        const width = Math.min(380, window.innerWidth - 24);
        popover.style.width = `${width}px`;
        let left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
        let top = rect.bottom + gap;
        const height = popover.offsetHeight || 220;
        if (top + height > window.innerHeight - 12) {
            top = Math.max(12, rect.top - height - gap);
        }
        if (rect.width > 0 && rect.right < width + 40 && window.innerWidth > 900) {
            left = Math.min(window.innerWidth - width - 12, rect.right + gap);
            top = Math.max(12, Math.min(rect.top, window.innerHeight - height - 12));
        }
        popover.style.left = `${left}px`;
        popover.style.top = `${top}px`;
    }

    function clearFocus() {
        main.querySelectorAll(".pv-guide-focus").forEach((element) => element.classList.remove("pv-guide-focus"));
    }

    function finishTour() {
        clearFocus();
        backdrop.hidden = true;
        popover.hidden = true;
        document.body.classList.remove("pv-guide-active");
        activeStep = -1;
        if (previousFocus && document.contains(previousFocus)) previousFocus.focus();
    }

    function showStep(index) {
        if (!steps.length) return;
        activeStep = Math.max(0, Math.min(index, steps.length - 1));
        clearFocus();
        const step = steps[activeStep];
        step.target.classList.add("pv-guide-focus");
        document.body.classList.add("pv-guide-active");
        backdrop.hidden = false;
        popover.hidden = false;
        popover.querySelector(".pv-guide-progress").textContent = `${text.step} ${activeStep + 1} ${text.of} ${steps.length}`;
        popover.querySelector(".pv-guide-step-title").textContent = step.title;
        popover.querySelector(".pv-guide-step-body").textContent = step.body;
        popover.querySelector(".pv-guide-back").disabled = activeStep === 0;
        popover.querySelector(".pv-guide-next").textContent = activeStep === steps.length - 1 ? text.done : text.next;
        step.target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
        window.setTimeout(() => {
            placePopover(step.target);
            popover.querySelector(".pv-guide-next").focus({ preventScroll: true });
        }, 250);
    }

    function startTour() {
        previousFocus = document.activeElement;
        steps = buildSteps();
        renderStepList();
        panel.addEventListener("hidden.bs.offcanvas", () => showStep(0), { once: true });
        panelController.hide();
    }

    openButton.setAttribute("aria-controls", panel.id);
    openButton.setAttribute("aria-expanded", "false");
    openButton.addEventListener("click", () => panelController.show());
    panel.addEventListener("shown.bs.offcanvas", () => openButton.setAttribute("aria-expanded", "true"));
    panel.addEventListener("hidden.bs.offcanvas", () => openButton.setAttribute("aria-expanded", "false"));
    panel.querySelector(".pv-guide-start").addEventListener("click", startTour);
    popover.querySelector(".pv-guide-exit").addEventListener("click", finishTour);
    popover.querySelector(".pv-guide-back").addEventListener("click", () => showStep(activeStep - 1));
    popover.querySelector(".pv-guide-next").addEventListener("click", () => {
        if (activeStep >= steps.length - 1) finishTour();
        else showStep(activeStep + 1);
    });
    window.addEventListener("resize", () => {
        if (activeStep >= 0 && steps[activeStep]) placePopover(steps[activeStep].target);
    });
    document.addEventListener("keydown", (event) => {
        if (activeStep < 0) return;
        if (event.key === "Escape") finishTour();
        if (event.key === "ArrowRight") showStep(activeStep + 1);
        if (event.key === "ArrowLeft") showStep(activeStep - 1);
    });

    main.querySelectorAll(".icon-only[title], .icon-button[title]").forEach((button) => {
        if (!button.getAttribute("aria-label")) button.setAttribute("aria-label", button.getAttribute("title"));
    });
    main.querySelectorAll("[required]").forEach((field) => field.setAttribute("aria-required", "true"));

    if (window.lucide) window.lucide.createIcons();
})();
