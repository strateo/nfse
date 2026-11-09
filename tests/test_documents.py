import unittest

from nfse_nacional.documents import dps_id, event_request_id, is_valid_cnpj, is_valid_cpf

ACCESS_KEY = "3550308" + "1" + "2" + "11222333000181" + "0" * 27


class CnpjTest(unittest.TestCase):
    def test_numeric(self):
        self.assertTrue(is_valid_cnpj("11222333000181"))
        self.assertFalse(is_valid_cnpj("11222333000182"))

    def test_alphanumeric_receita_example(self):
        # Example published by Receita Federal for the alphanumeric CNPJ (12.ABC.345/01DE-35).
        self.assertTrue(is_valid_cnpj("12ABC34501DE35"))
        self.assertFalse(is_valid_cnpj("12ABC34501DE36"))

    def test_rejects_malformed(self):
        for value in ("", "1122233300018", "11.222.333/0001-81", "12abc34501de35", "00000000000000", "12ABC34501DEA5"):
            self.assertFalse(is_valid_cnpj(value), value)


class CpfTest(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertTrue(is_valid_cpf("52998224725"))
        self.assertFalse(is_valid_cpf("52998224724"))
        self.assertFalse(is_valid_cpf("11111111111"))


class DpsIdTest(unittest.TestCase):
    def test_cnpj_layout(self):
        value = dps_id("3550308", cnpj="11222333000181", serie="900", n_dps="1")
        self.assertEqual(value, "DPS3550308" + "2" + "11222333000181" + "00900" + "0" * 14 + "1")
        self.assertEqual(len(value), 45)

    def test_cpf_is_tpinsc_1_and_left_padded(self):
        value = dps_id("3550308", cpf="52998224725", serie="1", n_dps="42")
        self.assertEqual(value[10:25], "1" + "00052998224725")

    def test_rejects_ambiguous_numbering(self):
        with self.assertRaises(ValueError):
            dps_id("3550308", cnpj="11222333000181", serie="1", n_dps="007")
        with self.assertRaises(ValueError):
            dps_id("3550308", cnpj="11222333000181", serie="90000", n_dps="1")

    def test_exactly_one_inscription(self):
        with self.assertRaises(ValueError):
            dps_id("3550308", serie="1", n_dps="1")


class EventIdTest(unittest.TestCase):
    def test_pre_id_has_no_sequence(self):
        value = event_request_id(ACCESS_KEY, "101101")
        self.assertEqual(value, "PRE" + ACCESS_KEY + "101101")
        self.assertEqual(len(value), 59)


if __name__ == "__main__":
    unittest.main()
