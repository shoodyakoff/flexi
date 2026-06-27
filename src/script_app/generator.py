from __future__ import annotations

import re
from dataclasses import dataclass

from .paths import JOB_SEARCH_LIBRARY_PATH, REELS_INSTRUCTION_PATH

FORBIDDEN_OPENERS = (
    "привет друзья",
    "всем привет",
    "сегодня расскажу",
    "в этом видео",
    "давайте поговорим",
    "давай разбер",
    "представь",
)


@dataclass(frozen=True)
class GeneratedVideoDraft:
    title: str
    hook: str
    voiceover: str
    cta: str
    caption_text: str
    pinned_comment: str
    notes: str


STAGE_LABELS = {
    "TOFU": "охват",
    "MOFU": "прогрев",
    "BOFU": "продажа",
}


def load_generation_knowledge() -> str:
    """Read the local rulebook and advice library for generated-video scripts."""
    return (
        REELS_INSTRUCTION_PATH.read_text(encoding="utf-8")
        + "\n\n"
        + JOB_SEARCH_LIBRARY_PATH.read_text(encoding="utf-8")
    )


def _normalize(value: str | None, default: str) -> str:
    normalized = (value or "").strip()
    return normalized or default


def _topic_kind(topic: str, context: str) -> str:
    text = f"{topic} {context}".lower()
    if any(token in text for token in ("атс", "ats", "робот", "автоотказ")):
        return "ats"
    if any(token in text for token in ("сопровод", "письм")):
        return "cover_letter"
    if any(token in text for token in ("отклик", "ваканси")):
        return "apply"
    return "resume"


def _hook(kind: str, stage: str) -> str:
    if stage == "BOFU":
        return "Ручная адаптация резюме и сопроводительного — это 30 МИНУТ. А жить когда?"
    if kind == "ats":
        return "Отправил отклик и получил АВТООТКАЗ? Показываю, где резюме ломается."
    if kind == "cover_letter":
        return "Написал сопроводительное с первой строки про себя. Получил ОТКАЗ. Почему?"
    if kind == "apply":
        return "Отправил одно резюме на 20 ВАКАНСИЙ. Рассказываю, что пошло не так."
    return "Во сколько лет узнал, что у рекрутёра 8 СЕКУНД на каждое резюме?"


def _voiceover(kind: str, stage: str, topic: str, context: str) -> str:
    if stage == "BOFU":
        return (
            "Один отклик руками — это открыть вакансию, сравнить с резюме, переписать нужные строки "
            "и придумать письмо. 30 минут. Потом ещё один. Потом ещё. В Сопроводе я добавляю резюме "
            "и вакансию, а через минуту получаю адаптированный документ и письмо. Проверяю глазами "
            "и отправляю без этого ручного болота."
        )
    if stage == "MOFU":
        return (
            "Раньше я открывал вакансию рядом с резюме и искал совпадения глазами. Это быстро "
            "превращается в таблицу боли. Не «работал с клиентами», а «вёл CRM и закрывал 40+ "
            "запросов в день». В Сопроводе я вставляю вакансию и резюме, а сервис подсвечивает, "
            "что переписать под конкретную роль."
        )
    if kind == "ats":
        return (
            "Автоотказ за пару минут — это часто не человек. Резюме сначала парсит система. "
            "Плохо: в вакансии CRM, а у тебя «клиентская база». Хорошо: «вёл CRM, обновлял сделки "
            "и собирал отчёты по воронке». Открой вакансию, забери 3–5 честных слов и вставь их "
            "в опыт, а не в мусорный список навыков."
        )
    if kind == "cover_letter":
        return (
            "Сопроводительное не должно пересказывать резюме. Плохо: «меня заинтересовала ваша "
            "вакансия». Это мёртвая первая строка. Хорошо: «вы ищете человека, который наведёт "
            "порядок в CRM — я уже делал это в отделе продаж на 12 менеджеров». Сначала задача "
            "работодателя, потом твой опыт."
        )
    if kind == "apply":
        return (
            "Массовый отклик выглядит эффективно только в голове. Плохо: одно резюме на маркетолога, "
            "продакта и project manager. Хорошо: открыл вакансию, выделил 5 требований, переписал "
            "верхний блок и 2 пункта опыта. Не «занимался рекламой», а «снизил стоимость лида "
            "с 4200 до 1800 рублей»."
        )
    return (
        "Рекрутёр не читает резюме как роман. Он сканирует первый экран. Плохо: «ответственный, "
        "коммуникабельный, быстро обучаюсь». Хорошо: «B2B-маркетолог, CRM, воронки, аналитика, "
        "поднял конверсию из лида в продажу с 18% до 27%». Мини-шаблон простой: роль, 2 навыка "
        "из вакансии и один результат в цифрах."
    )


def _cta(cta_type: str, stage: str, kind: str) -> tuple[str, str | None]:
    if cta_type == "keyword":
        keyword = {"ats": "АТС", "cover_letter": "СОПРОВОД", "apply": "ОТКЛИК"}.get(kind, "РЕЗЮМЕ")
        return f"Напиши в комментариях слово {keyword} — пришлю чек-лист в личку.", keyword
    if cta_type == "link":
        if stage == "TOFU":
            return "Проверь своё РЕЗЮМЕ по ссылке в шапке профиля. Бесплатно.", None
        return "Адаптировать резюме за 3 минуты — ССЫЛКА в шапке профиля.", None
    return "Сохрани, чтобы проверить своё РЕЗЮМЕ перед следующим откликом.", None


def _caption_cta(cta_type: str, keyword: str | None) -> str:
    if cta_type == "keyword" and keyword:
        return f"Напиши «{keyword}» в комментариях, если хочешь забрать мини-чек-лист."
    if cta_type == "link":
        return "Ссылка в шапке профиля, если хочешь проверить резюме без ручного ковыряния."
    return "Сохрани и проверь перед следующим откликом."


def _caption(kind: str, stage: str, cta_type: str, keyword: str | None) -> str:
    action = _caption_cta(cta_type, keyword)
    if stage == "BOFU":
        return (
            "Ручная адаптация выглядит полезной только пока у тебя одна вакансия.\n\n"
            "На третьей уже начинается боль: сравнить требования, переписать верхний блок, "
            "добавить нужные слова, придумать письмо и не отправить всё это в состоянии "
            "«ну вроде нормально».\n\n"
            "Мини-проверка перед откликом:\n"
            "1. В резюме есть слова из конкретной вакансии.\n"
            "2. Верхний блок отвечает на вопрос: почему ты подходишь именно сюда.\n"
            "3. Сопроводительное начинается с задачи работодателя, а не с «меня заинтересовала».\n\n"
            f"{action}"
        )
    if kind == "ats":
        return (
            "Автоотказ через пару минут почти никогда не про твою личность.\n\n"
            "Часто резюме сначала читает система. Она не понимает «я классный». "
            "Она ищет совпадения: должность, инструменты, требования, формулировки из вакансии.\n\n"
            "Быстрая проверка:\n"
            "1. Открой вакансию.\n"
            "2. Выпиши 3-5 честных ключевых слов.\n"
            "3. Найди, где этот опыт реально есть в резюме.\n"
            "4. Вставь слова естественно в опыт, а не отдельной кучей в навыки.\n\n"
            f"{action}"
        )
    if kind == "cover_letter":
        return (
            "Сопроводительное умирает в первой строке чаще, чем кажется.\n\n"
            "Плохо: «меня заинтересовала ваша вакансия». Так начинается тысяча писем.\n\n"
            "Лучше: «вы ищете человека, который наведёт порядок в CRM — я уже делал это "
            "в отделе продаж на 12 менеджеров».\n\n"
            "Формула простая:\n"
            "задача работодателя -> твой похожий опыт -> один конкретный результат.\n\n"
            f"{action}"
        )
    if kind == "apply":
        return (
            "Массовый отклик кажется эффективным, пока не приходят массовые отказы.\n\n"
            "Проблема не в том, что резюме плохое вообще. Проблема в том, что оно часто "
            "не отвечает на конкретную вакансию.\n\n"
            "Перед отправкой проверь:\n"
            "1. Верхний блок совпадает с ролью.\n"
            "2. В опыте есть 2-3 требования из вакансии.\n"
            "3. Есть хотя бы одна цифра, а не только обязанности.\n\n"
            "Плохо: «занимался рекламой».\n"
            "Лучше: «снизил стоимость лида с 4200 до 1800 рублей».\n\n"
            f"{action}"
        )
    return (
        "Первый экран резюме решает больше, чем хочется признавать.\n\n"
        "Рекрутёр не читает его как роман. Он сканирует: кто ты, под какую роль, "
        "какие навыки совпадают и где доказательство в цифрах.\n\n"
        "Плохо: «ответственный, коммуникабельный, быстро обучаюсь».\n\n"
        "Лучше: «B2B-маркетолог | CRM, воронки, аналитика | поднял конверсию "
        "из лида в продажу с 18% до 27%».\n\n"
        "Шаблон:\n"
        "роль -> 2-3 навыка из вакансии -> один результат в цифрах.\n\n"
        f"{action}"
    )


def _pinned_comment(kind: str, stage: str) -> str:
    if stage == "BOFU":
        return "Что бесит больше: переписывать резюме или каждый раз придумывать сопроводительное?"
    if kind == "ats":
        return "У тебя чаще бывает тишина после отклика или быстрый автоотказ?"
    if kind == "cover_letter":
        return "Какая первая строка сопроводительного хуже: «меня заинтересовала» или «прошу рассмотреть»?"
    if kind == "apply":
        return "Сколько версий резюме у тебя сейчас: одна на всё или отдельные под разные роли?"
    return "Что сложнее написать в резюме: кто ты, навыки или достижения в цифрах?"


def _has_caps_accent(text: str) -> bool:
    return bool(re.search(r"\b[А-ЯЁA-Z0-9]{3,}(?:[- ][А-ЯЁA-Z0-9]{2,})?\b", text))


def validate_generated_script(
    stage: str,
    hook: str,
    voiceover: str,
    cta: str,
    caption_text: str = "",
    pinned_comment: str = "",
) -> list[str]:
    problems: list[str] = []
    combined_start = f"{hook} {voiceover[:80]}".lower()
    for opener in FORBIDDEN_OPENERS:
        if opener in combined_start:
            problems.append(f"запрещённое начало: {opener}")
    if not _has_caps_accent(hook):
        problems.append("в хуке нет акцентного слова капсом")
    if not _has_caps_accent(cta):
        problems.append("в призыве нет акцентного слова капсом")
    if stage == "TOFU" and "сопровод" in f"{hook} {voiceover}".lower():
        problems.append("на этапе охвата хук и основной текст не должны упоминать Сопровод")
    if stage == "MOFU" and voiceover.lower().count("сопровод") != 1:
        problems.append("на этапе прогрева основной текст должен упомянуть Сопровод ровно один раз")
    if caption_text and len(caption_text.strip()) < 180:
        problems.append("описание слишком короткое")
    if pinned_comment and "?" not in pinned_comment:
        problems.append("закреплённый комментарий должен приглашать к ответу")
    return problems


def generate_generated_video_script(
    *,
    topic: str,
    funnel_stage: str = "TOFU",
    cta_type: str = "save",
    format_name: str = "основной",
    context: str = "",
) -> GeneratedVideoDraft:
    # The returned text is deterministic for MVP, but this read is intentional:
    # changes in the local rulebooks are part of the generation contract.
    knowledge = load_generation_knowledge()
    stage = _normalize(funnel_stage, "TOFU").upper()
    normalized_cta_type = _normalize(cta_type, "save").lower()
    normalized_topic = _normalize(topic, "первый экран резюме")
    normalized_context = _normalize(context, "")
    kind = _topic_kind(normalized_topic, normalized_context)
    hook = _hook(kind, stage)
    voiceover = _voiceover(kind, stage, normalized_topic, normalized_context)
    cta, keyword = _cta(normalized_cta_type, stage, kind)
    caption_text = _caption(kind, stage, normalized_cta_type, keyword)
    pinned_comment = _pinned_comment(kind, stage)
    problems = validate_generated_script(
        stage,
        hook,
        voiceover,
        cta,
        caption_text,
        pinned_comment,
    )
    stage_label = STAGE_LABELS.get(stage, stage)
    notes = (
        f"Сгенерировано на основе {REELS_INSTRUCTION_PATH.name} и {JOB_SEARCH_LIBRARY_PATH.name}. "
        f"Включены описание и закреплённый комментарий, без ответов на комментарии. "
        f"Формат: {format_name}. Объём базы знаний: {len(knowledge)} символов."
    )
    if problems:
        notes += " Предупреждения проверки: " + "; ".join(problems)
    return GeneratedVideoDraft(
        title=f"{stage_label} — {normalized_topic}",
        hook=hook,
        voiceover=voiceover,
        cta=cta,
        caption_text=caption_text,
        pinned_comment=pinned_comment,
        notes=notes,
    )
