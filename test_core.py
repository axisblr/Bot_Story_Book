import unittest

from core import (
    calc_complexity,
    calc_cost,
    clean_filename,
    min_age_from_text,
    parse_customer_and_child,
    safe_counts,
)


class TestCore(unittest.TestCase):
    def test_clean_filename(self):
        self.assertEqual(clean_filename("Мама Оля"), "Мама Оля")
        self.assertEqual(clean_filename('Дед/Ваня*?:"<>|'), "ДедВаня")

    def test_parse_customer_and_child(self):
        self.assertEqual(
            parse_customer_and_child("Иванов Иван (Миша) @nick"),
            ("Иванов Иван @nick", "Миша"),
        )
        self.assertEqual(parse_customer_and_child("Иванов Иван"), ("Иванов Иван", "Не указано"))
        self.assertEqual(parse_customer_and_child(""), ("", "Не указано"))

    def test_min_age_from_text(self):
        self.assertEqual(min_age_from_text("5 лет"), 5)
        self.assertEqual(min_age_from_text("7 и 3 года"), 3)
        self.assertEqual(min_age_from_text("не помню"), 0)

    def test_complexity(self):
        # малыш, один главный герой, мало персонажей -> минимум
        self.assertEqual(calc_complexity(min_age=2, main_count=1, total_count=5), 2 + 1 + 2)
        # старше 3 лет
        self.assertEqual(calc_complexity(min_age=7, main_count=1, total_count=5), 3 + 1 + 2)
        # несколько главных героев
        self.assertEqual(calc_complexity(min_age=2, main_count=2, total_count=5), 2 + 3 + 2)
        # границы по количеству персонажей
        self.assertEqual(calc_complexity(2, 1, 7), 2 + 1 + 2)
        self.assertEqual(calc_complexity(2, 1, 8), 2 + 1 + 3)
        self.assertEqual(calc_complexity(2, 1, 12), 2 + 1 + 3)
        self.assertEqual(calc_complexity(2, 1, 13), 2 + 1 + 4)

    def test_cost_basic(self):
        self.assertEqual(calc_cost(main_count=1, total_count=5), 50)
        self.assertEqual(calc_cost(main_count=1, total_count=8), 60)

    def test_cost_extra_main_characters(self):
        # 2 главных героя: 50 + 25
        self.assertEqual(calc_cost(main_count=2, total_count=5), 75)
        # 3 главных героя: 50 + 50
        self.assertEqual(calc_cost(main_count=3, total_count=5), 100)

    def test_cost_many_characters_surcharge(self):
        # больше 12 героев: база 60 + надбавка 20
        self.assertEqual(calc_cost(main_count=1, total_count=13), 80)
        # ровно 12 — надбавки нет
        self.assertEqual(calc_cost(main_count=1, total_count=12), 60)

    def test_safe_counts(self):
        self.assertEqual(safe_counts("2", "9"), (2, 9))
        self.assertEqual(safe_counts("два", "девять"), (1, 1))
        self.assertEqual(safe_counts(None, None), (1, 1))
        self.assertEqual(safe_counts("0", "-5"), (1, 1))


if __name__ == "__main__":
    unittest.main()
