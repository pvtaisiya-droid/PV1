from __future__ import annotations

from dataclasses import dataclass


WHO_UMC_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("certain", "Определённая (Certain)"),
    ("probable_likely", "Вероятная / правдоподобная (Probable / Likely)"),
    ("possible", "Возможная (Possible)"),
    ("unlikely", "Сомнительная / маловероятная (Unlikely)"),
    ("conditional_unclassified", "Условная / неклассифицированная (Conditional / Unclassified)"),
    ("unassessable_unclassifiable", "Не поддаётся оценке / неклассифицируемая (Unassessable / Unclassifiable)"),
)
WHO_UMC_VALUES = {value for value, _ in WHO_UMC_CATEGORIES}
WHO_UMC_DESCRIPTIONS = {
    "certain": (
        "Клинические проявления НР или нарушения лабораторных показателей возникают в период "
        "приёма препарата и не могут быть объяснены существующими заболеваниями, другими "
        "лекарствами или иными факторами. Проявления НПР правдоподобно регрессируют после "
        "отмены препарата. Событие должно быть определённым фармакологически или "
        "феноменологически; результат повторного назначения должен подтверждать связь, если "
        "повторное назначение проводилось и было необходимо."
    ),
    "probable_likely": (
        "Клинические проявления НР или нарушения лабораторных показателей связаны по времени "
        "с приёмом лекарства, вряд ли относятся к сопутствующим заболеваниям или другим "
        "факторам и клинически обоснованно регрессируют после отмены препарата. Информация о "
        "повторном назначении препарата для этой категории не требуется."
    ),
    "possible": (
        "Клинические проявления НР или изменения лабораторных показателей связаны по времени "
        "с приёмом препарата, но их также можно объяснить сопутствующими заболеваниями, "
        "другими лекарствами или воздействием химических соединений. Информация о реакции на "
        "отмену лекарства отсутствует, недостаточна или неясна."
    ),
    "unlikely": (
        "Клинические проявления НР или изменения лабораторных показателей возникают при "
        "временной связи с приёмом лекарства, которая делает причинную связь маловероятной, "
        "хотя и не исключает её полностью. Другие лекарства, заболевания, химические вещества "
        "или иные факторы представляют более правдоподобное объяснение."
    ),
    "conditional_unclassified": (
        "Клинические проявления НР или нарушения лабораторных показателей, отнесённые к НПР, "
        "пока трудно оценить. Для надлежащей оценки необходимы дополнительные данные либо "
        "полученные дополнительные данные в настоящее время анализируются."
    ),
    "unassessable_unclassifiable": (
        "Сообщение о подозреваемой НР нельзя оценить, поскольку информация недостаточна или "
        "противоречива и не может быть дополнена либо проверена."
    ),
}
CAUSALITY_RESULT_LABELS = {
    **dict(WHO_UMC_CATEGORIES),
    "definite": "Определённая (Definite)",
    "probable": "Вероятная (Probable)",
    "possible": "Возможная (Possible)",
    "doubtful": "Сомнительная (Doubtful)",
}

ASSESSOR_ROLES: tuple[tuple[str, str], ...] = (
    ("dru", "ДРУ"),
    ("ars", "АРС"),
    ("regulator", "Регулятор"),
    ("other", "Другое лицо / организация"),
)
ASSESSOR_ROLE_VALUES = {value for value, _ in ASSESSOR_ROLES}


@dataclass(frozen=True)
class NaranjoQuestion:
    key: str
    text: str
    yes_score: int
    no_score: int
    unknown_score: int = 0


# The prompts are concise Russian paraphrases of the original Naranjo algorithm.
NARANJO_QUESTIONS: tuple[NaranjoQuestion, ...] = (
    NaranjoQuestion("q1", "Есть ли убедительные публикации о такой реакции?", 1, 0),
    NaranjoQuestion("q2", "Возникла ли реакция после применения подозреваемого препарата?", 2, -1),
    NaranjoQuestion("q3", "Уменьшилась ли реакция после отмены препарата или введения антагониста?", 1, 0),
    NaranjoQuestion("q4", "Возникла ли реакция повторно после повторного назначения препарата?", 2, -1),
    NaranjoQuestion("q5", "Существуют ли альтернативные причины, способные вызвать реакцию?", -1, 2),
    NaranjoQuestion("q6", "Возникла ли реакция повторно при применении плацебо?", -1, 1),
    NaranjoQuestion("q7", "Обнаружена ли токсическая концентрация препарата в биологических средах?", 1, 0),
    NaranjoQuestion("q8", "Усиливалась ли реакция при повышении дозы или ослабевала при её снижении?", 1, 0),
    NaranjoQuestion("q9", "Была ли ранее сходная реакция на этот или аналогичный препарат?", 1, 0),
    NaranjoQuestion("q10", "Подтверждена ли реакция объективными данными?", 1, 0),
)
NARANJO_ANSWER_VALUES = {"yes", "no", "unknown"}


def calculate_naranjo_score(answers: dict[str, str]) -> int:
    score = 0
    for question in NARANJO_QUESTIONS:
        answer = (answers.get(question.key) or "").strip().lower()
        if answer not in NARANJO_ANSWER_VALUES:
            raise ValueError(f"Answer for {question.key} must be yes, no, or unknown.")
        if answer == "yes":
            score += question.yes_score
        elif answer == "no":
            score += question.no_score
        else:
            score += question.unknown_score
    return score


def classify_naranjo_score(score: int) -> str:
    if score >= 9:
        return "definite"
    if score >= 5:
        return "probable"
    if score >= 1:
        return "possible"
    return "doubtful"
