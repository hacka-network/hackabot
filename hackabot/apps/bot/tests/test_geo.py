import pytest
from django.test import Client, RequestFactory

from hackabot.apps.bot import geo
from hackabot.apps.bot.models import Node

BARCELONA = (41.3874, 2.1686)
LONDON = (51.5074, -0.1278)
LYON = (45.7640, 4.8357)
PARIS = (48.8566, 2.3522)
SYDNEY = (-33.8688, 151.2093)


class FakeReader:
    def __init__(self, by_ip):
        self.by_ip = by_ip

    def get(self, ip):
        if ip == "malformed":
            raise ValueError("bad address")
        return self.by_ip.get(ip)


def city(lat, lon):
    return dict(location=dict(latitude=lat, longitude=lon))


@pytest.fixture
def reader(request):
    def install(by_ip):
        geo._reader = FakeReader(by_ip)
        geo._reader_tried = True

    yield install
    geo._reader = None
    geo._reader_tried = False


@pytest.fixture
def no_reader():
    geo._reader = None
    geo._reader_tried = True
    yield
    geo._reader_tried = False


def make_request(**meta):
    return RequestFactory().get("/api/nodes/", **meta)


class TestClientIp:
    def test_uses_remote_addr_without_forwarding(self):
        request = make_request(REMOTE_ADDR="203.0.113.5")
        assert geo.client_ip(request) == "203.0.113.5"

    def test_takes_last_forwarded_entry(self):
        # Heroku appends the real peer, so anything a client prepends must
        # not win.
        request = make_request(
            HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8, 9.9.9.9",
            REMOTE_ADDR="10.0.0.1",
        )
        assert geo.client_ip(request) == "9.9.9.9"

    def test_single_forwarded_entry(self):
        request = make_request(HTTP_X_FORWARDED_FOR="9.9.9.9")
        assert geo.client_ip(request) == "9.9.9.9"

    def test_missing_everything(self):
        assert geo.client_ip(make_request(REMOTE_ADDR="")) == ""


class TestDistance:
    def test_known_distance(self):
        km = geo.distance_km(*LONDON, *PARIS)
        assert 335 < km < 350

    def test_same_point_is_zero(self):
        assert geo.distance_km(*PARIS, *PARIS) == pytest.approx(0)

    def test_symmetric(self):
        there = geo.distance_km(*LONDON, *SYDNEY)
        back = geo.distance_km(*SYDNEY, *LONDON)
        assert there == pytest.approx(back)


class TestLocate:
    def test_returns_coordinates(self, reader):
        reader(dict(and_so=city(*PARIS)))
        assert geo.locate("and_so") == PARIS

    def test_unknown_ip(self, reader):
        reader({})
        assert geo.locate("198.51.100.1") is None

    def test_record_without_location(self, reader):
        reader(dict(bare=dict(country=dict(iso_code="FR"))))
        assert geo.locate("bare") is None

    def test_partial_location(self, reader):
        reader(dict(half=dict(location=dict(latitude=48.8))))
        assert geo.locate("half") is None

    def test_malformed_ip_does_not_raise(self, reader):
        reader({})
        assert geo.locate("malformed") is None

    def test_empty_ip(self, reader):
        reader({})
        assert geo.locate("") is None

    def test_no_database(self, no_reader):
        assert geo.locate("9.9.9.9") is None


@pytest.fixture
def nodes(db):
    Node.objects.all().delete()
    return dict(
        barcelona=Node.objects.create(
            name="Hackalona", lat=BARCELONA[0], lon=BARCELONA[1]
        ),
        paris=Node.objects.create(name="Hackaris", lat=PARIS[0], lon=PARIS[1]),
        sydney=Node.objects.create(
            name="Hackaoz", lat=SYDNEY[0], lon=SYDNEY[1]
        ),
    )


class TestNearestNode:
    def test_picks_the_closest(self, reader, nodes):
        reader(dict(visitor=city(*LONDON)))
        request = make_request(REMOTE_ADDR="visitor")
        assert geo.nearest_node(request, Node.objects.all()) == nodes["paris"]

    def test_returns_none_beyond_the_cutoff(self, reader, nodes):
        # Nairobi is thousands of km from every node in the fixture.
        reader(dict(visitor=city(-1.2921, 36.8219)))
        request = make_request(REMOTE_ADDR="visitor")
        assert geo.nearest_node(request, Node.objects.all()) is None

    def test_skips_disabled_nodes(self, reader, nodes):
        nodes["paris"].disabled = True
        nodes["paris"].save()
        # Lyon has Paris nearest and Barcelona second, both inside the
        # cutoff, so the fallback is a real choice rather than the only one.
        reader(dict(visitor=city(*LYON)))
        request = make_request(REMOTE_ADDR="visitor")
        assert (
            geo.nearest_node(request, Node.objects.all()) == nodes["barcelona"]
        )

    def test_skips_nodes_without_coordinates(self, reader, nodes):
        Node.objects.filter(pk=nodes["paris"].pk).update(lat=None, lon=None)
        reader(dict(visitor=city(*LYON)))
        request = make_request(REMOTE_ADDR="visitor")
        assert (
            geo.nearest_node(request, Node.objects.all()) == nodes["barcelona"]
        )

    def test_no_location_means_no_node(self, reader, nodes):
        reader({})
        request = make_request(REMOTE_ADDR="198.51.100.7")
        assert geo.nearest_node(request, Node.objects.all()) is None

    def test_no_database_means_no_node(self, no_reader, nodes):
        request = make_request(REMOTE_ADDR="9.9.9.9")
        assert geo.nearest_node(request, Node.objects.all()) is None


class TestApiNearestFlag:
    def test_flags_exactly_one_node(self, reader, nodes):
        reader(dict(visitor=city(*LONDON)))
        response = Client().get("/api/nodes/", REMOTE_ADDR="visitor")

        flagged = [n for n in response.json()["nodes"] if n["nearest"]]
        assert [n["name"] for n in flagged] == ["Hackaris"]

    def test_no_flag_without_a_database(self, no_reader, nodes):
        response = Client().get("/api/nodes/", REMOTE_ADDR="9.9.9.9")

        assert not any(n["nearest"] for n in response.json()["nodes"])

    def test_response_is_not_cacheable(self, no_reader, nodes):
        response = Client().get("/api/nodes/")
        assert response["Cache-Control"] == "private, no-store"
