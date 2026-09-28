from src.hello import hello


def test_hello():
    assert hello() == 'world'
