"""Чистая бизнес-логика заказа: разбор данных клиента, сложность и цена.

Вынесено из обработчиков, чтобы правила можно было менять и тестировать,
не трогая телеграм-код.
"""

import re
from typing import Tuple

# --- Параметры ценообразования (BYN) ---
BASE_COST_SMALL = 50  # до 7 героев включительно
BASE_COST_LARGE = 60  # больше 7 героев
EXTRA_MAIN_CHAR_RATE = 0.5  # каждый доп. главный герой = +50% базовой цены
MANY_CHARS_SURCHARGE = 20  # надбавка, если героев больше 12
MANY_CHARS_THRESHOLD = 12
LARGE_ORDER_THRESHOLD = 7


def clean_filename(text: str) -> str:
    """Убирает символы, недопустимые в именах файлов."""
    return re.sub(r'[\\/*?:"<>|]', "", text)


def parse_customer_and_child(full_name_str: str) -> Tuple[str, str]:
    """Разбирает строку вида «Иванов Иван (Миша) @nick».

    Возвращает (заказчик, имя ребёнка). Если скобок нет — вся строка считается
    заказчиком, а имя ребёнка неизвестно.
    """
    match = re.search(r"^(.*?)\s*\((.*?)\)\s*(.*)$", full_name_str or "")
    if match:
        customer = f"{match.group(1).strip()} {match.group(3).strip()}".strip()
        child = match.group(2).strip()
        return customer, child
    return (full_name_str or "").strip(), "Не указано"


def min_age_from_text(age_str: str) -> int:
    """Минимальный возраст из произвольного текста («5 и 7 лет» -> 5)."""
    ages = [int(x) for x in re.findall(r"\d+", age_str or "")]
    return min(ages) if ages else 0


def calc_complexity(min_age: int, main_count: int, total_count: int) -> int:
    """Оценка сложности заказа в «звёздах»."""
    complexity = 0
    complexity += 2 if min_age <= 3 else 3
    complexity += 1 if main_count == 1 else 3
    if total_count <= LARGE_ORDER_THRESHOLD:
        complexity += 2
    elif total_count <= MANY_CHARS_THRESHOLD:
        complexity += 3
    else:
        complexity += 4
    return complexity


def calc_cost(main_count: int, total_count: int) -> int:
    """Расчётная стоимость книги в BYN."""
    base = BASE_COST_LARGE if total_count > LARGE_ORDER_THRESHOLD else BASE_COST_SMALL
    cost = base
    if main_count > 1:
        cost = base + (main_count - 1) * (base * EXTRA_MAIN_CHAR_RATE)
    if total_count > MANY_CHARS_THRESHOLD:
        cost += MANY_CHARS_SURCHARGE
    return int(cost)


def safe_counts(main_raw, total_raw) -> Tuple[int, int]:
    """Аккуратно приводит количества героев к числам (клиент мог написать текстом)."""
    try:
        main_count = int(main_raw)
    except (TypeError, ValueError):
        main_count = 1
    try:
        total_count = int(total_raw)
    except (TypeError, ValueError):
        total_count = 1
    return max(1, main_count), max(1, total_count)
