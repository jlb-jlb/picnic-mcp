import unittest

from picnic_mcp import finder


def product(pid, name, cents, size):
    return {"id": pid, "name": name, "price_cents": cents, "unit_quantity": size}


CARROTS = [
    product("a", "Möhren", 134, "1kg"),
    product("b", "Bio Möhren", 170, "1kg"),
    product("c", "Bio Möhren Mini", 116, "200g"),
    product("d", "Möhren", 188, "2kg"),
    product("e", "Möhren-Mix", 224, "750g"),
]


class ParsePackTest(unittest.TestCase):
    def test_reads_the_size_formats_picnic_uses(self):
        self.assertEqual(finder.parse_pack("500g"), (500, "g", False))
        self.assertEqual(finder.parse_pack("1,5L"), (1500, "ml", False))
        self.assertEqual(finder.parse_pack("6 x 750ml"), (4500, "ml", False))
        self.assertEqual(finder.parse_pack("2x150g"), (300, "g", False))
        self.assertEqual(finder.parse_pack("ca. 120g"), (120, "g", True))
        self.assertEqual(finder.parse_pack("1 Bund"), (1, "piece", False))
        self.assertEqual(finder.parse_pack("10er Pack | Mind. Größe S"), (10, "piece", False))
        self.assertEqual(finder.parse_pack("1 Stück Gr. L"), (1, "piece", False))

    def test_returns_none_without_a_size(self):
        self.assertIsNone(finder.parse_pack(None))
        self.assertIsNone(finder.parse_pack("25 WL"))

    def test_amounts(self):
        self.assertEqual(finder.parse_amount("300 g"), (300, "g"))
        self.assertEqual(finder.parse_amount("1,5 kg"), (1500, "g"))
        self.assertEqual(finder.parse_amount("0.5 l"), (500, "ml"))
        self.assertEqual(finder.parse_amount(3), (3, "piece"))
        self.assertEqual(finder.parse_amount("3"), (3, "piece"))
        for bad in ("lots", "-2 g", "3 cups", True, 0):
            with self.assertRaises(ValueError):
                finder.parse_amount(bad)

    def test_organic_is_read_from_the_name(self):
        self.assertTrue(finder.is_organic("Edeka Bio Bio Spaghetti"))
        self.assertTrue(finder.is_organic("Demeter Hafer"))
        self.assertFalse(finder.is_organic("Biscuit Butterkeks"))
        self.assertFalse(finder.is_organic(None))


class FindTest(unittest.TestCase):
    def test_cheapest_covers_the_amount_for_the_lowest_total(self):
        result = finder.find(CARROTS, "möhren", amount="600 g")
        self.assertEqual(result["best"]["id"], "a")
        self.assertEqual(result["best"]["packs"], 1)
        self.assertEqual(result["best"]["total_cents"], 134)
        self.assertEqual(result["best"]["leftover"], 400)

    def test_small_amount_prefers_the_small_pack_when_it_is_cheaper_in_total(self):
        result = finder.find(CARROTS, "möhren", amount="150 g", preference="organic")
        self.assertEqual(result["best"]["id"], "c")
        self.assertEqual(result["best"]["total_cents"], 116)

    def test_organic_only_returns_organic_and_cheapest_first(self):
        result = finder.find(CARROTS, "möhren", preference="organic")
        self.assertEqual(result["best"]["id"], "c")
        self.assertTrue(all(o["organic"] for o in [result["best"]] + result["alternatives"]))

    def test_organic_falls_back_with_a_note_when_there_is_none(self):
        result = finder.find([CARROTS[0], CARROTS[3]], "möhren", preference="organic")
        self.assertEqual(result["best"]["id"], "a")
        self.assertIn("no organic", result["notes"][0])

    def test_conventional_skips_organic(self):
        result = finder.find(CARROTS, "möhren", preference="conventional")
        self.assertFalse(result["best"]["organic"])

    def test_best_value_uses_price_per_kilo(self):
        result = finder.find(CARROTS, "möhren", preference="best_value")
        self.assertEqual(result["best"]["id"], "d")
        self.assertEqual(result["best"]["base_price"], "0.94 EUR/kg")

    def test_pack_size_preferences(self):
        self.assertEqual(finder.find(CARROTS, "möhren", preference="smallest_pack")["best"]["id"], "c")
        self.assertEqual(finder.find(CARROTS, "möhren", preference="largest_pack")["best"]["id"], "d")

    def test_exclude_drops_products_by_word(self):
        result = finder.find(CARROTS, "möhren", exclude=["mix", "mini"])
        names = [result["best"]["name"]] + [a["name"] for a in result["alternatives"]]
        self.assertNotIn("Möhren-Mix", names)
        self.assertNotIn("Bio Möhren Mini", names)

    def test_loose_match_is_flagged_when_no_name_has_every_word(self):
        result = finder.find(CARROTS, "karotten")
        self.assertEqual(result["match"], "loose")
        self.assertTrue(result["notes"])

    def test_no_products_gives_no_best(self):
        result = finder.find([], "möhren")
        self.assertIsNone(result["best"])
        self.assertEqual(result["match"], "none")

    def test_mismatched_units_are_reported_not_guessed(self):
        eggs = [product("x", "Eier", 229, "6er Pack")]
        result = finder.find(eggs, "eier", amount="300 g")
        self.assertEqual(result["best"]["id"], "x")
        self.assertNotIn("packs", result["best"])
        self.assertIn("could not be turned into packs", result["notes"][0])

    def test_pieces_become_packs(self):
        eggs = [product("x", "Eier", 229, "6er Pack"), product("y", "Eier", 249, "10er Pack")]
        result = finder.find(eggs, "eier", amount="8")
        self.assertEqual(result["best"]["id"], "y")
        self.assertEqual(result["best"]["packs"], 1)

    def test_rejects_an_unknown_preference(self):
        with self.assertRaises(ValueError):
            finder.find(CARROTS, "möhren", preference="tastiest")


if __name__ == "__main__":
    unittest.main()
