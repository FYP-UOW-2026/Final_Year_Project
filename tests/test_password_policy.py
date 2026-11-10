import pytest

from bioaudit.password_policy import password_strength_problem as p


@pytest.mark.parametrize("pw", ["Short1!a", "alllowercase123!", "ALLUPPERCASE123!",
                                "NoDigitsHere!!!!", "NoSymbols1234Abc", "Password1234!"[:0] + "aaaaAAAA1111"])
def test_rejects_weak(pw):
    assert p(pw) is not None


def test_rejects_common_word_dressed_up():
    assert p("PASSWORD1234!!") is not None


def test_accepts_strong():
    assert p("Tr1cky-Horse-Battery") is None
