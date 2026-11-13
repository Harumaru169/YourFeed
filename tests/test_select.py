from yourfeed.pipeline.select import allocate, by_site, fill_by_weight, matches


def test_allocate_uses_largest_remainder_and_keeps_order_on_ties():
    assert allocate({"a": 15, "b": 15, "c": 2, "d": 2}, 34) == {"a": 15, "b": 15, "c": 2, "d": 2}
    assert allocate({"a": 1, "b": 1, "c": 1}, 2) == {"a": 1, "b": 1, "c": 0}
    assert allocate({"a": 0, "b": 0}, 5) == {"a": 0, "b": 0}
    assert sum(allocate({"a": 3, "b": 7, "c": 11}, 50).values()) == 50


def test_terms_match_words_in_english_and_substrings_in_japanese():
    assert matches("New AI model released", ["ai"])
    assert not matches("Said the chair", ["ai"])
    assert matches("量子コンピュータの新しい成果", ["量子"])
    assert matches("ＧＤＰ速報", ["gdp"])
    assert not matches("anything", ["", "  "])


def take_from(available, taken, used=None):
    def fill(key, target, window):
        if used is not None:
            used.append((key, window))
        while taken[key] < target and available[key][window]:
            available[key][window] -= 1
            taken[key] += 1
        return taken[key] >= target
    return fill


def test_fill_by_weight_widens_each_window_before_moving_a_short_share():
    available = {"a": {"new": 2, "old": 5}, "b": {"new": 10, "old": 0}, "c": {"new": 0, "old": 0}}
    taken = {"a": 0, "b": 0, "c": 0}
    used = []
    targets = fill_by_weight({"a": 3, "b": 1, "c": 2, "off": 0}, 12, ["new", "old"], taken.get,
                             take_from(available, taken, used))
    # a fills its 6 from both windows; c finds nothing, so its 4 go to a and b by weight, and the one
    # a cannot fill goes to b.
    assert taken == {"a": 7, "b": 5, "c": 0} and sum(taken.values()) == 12
    assert targets["off"] == 0 and ("off", "new") not in used
    assert used.index(("a", "old")) < used.index(("b", "new"), 1)


def test_fill_by_weight_never_fills_past_the_total():
    # Both run short in the first window and find plenty in the second.
    available = {"a": {"new": 1, "old": 100}, "b": {"new": 6, "old": 100}}
    taken = {"a": 0, "b": 0}
    fill_by_weight({"a": 1, "b": 1}, 10, ["new", "old"], taken.get, take_from(available, taken), step=1)
    assert taken == {"a": 5, "b": 5}


def test_fill_by_weight_can_keep_each_share_to_its_key():
    available = {"a": {"w": 1}, "b": {"w": 100}}
    taken = {"a": 0, "b": 0}
    fill_by_weight({"a": 1, "b": 1}, 10, ["w"], taken.get, take_from(available, taken), move_short=False)
    assert taken == {"a": 1, "b": 5}


def test_fill_by_weight_uses_the_wider_window_when_the_first_runs_out():
    available = {"a": {"new": 1, "old": 5}}
    taken = {"a": 0}

    def fill(key, target, window):
        while taken[key] < target and available[key][window]:
            available[key][window] -= 1
            taken[key] += 1
        return taken[key] >= target

    fill_by_weight({"a": 1}, 4, ["new", "old"], taken.get, fill)
    assert taken == {"a": 4} and available["a"] == {"new": 0, "old": 2}


def test_by_site_alternates_sites_in_order():
    items = [("a1", "https://a.example/1"), ("a2", "https://www.a.example/2"), ("a3", "https://a.example/3"),
             ("b1", "https://b.example/1")]
    assert by_site(items) == ["a1", "b1", "a2", "a3"]


def test_steps_take_turns_by_progress():
    order = []

    def fill(key, target, window):
        order.append(key)
        return True

    counts = lambda key: order.count(key)  # noqa: E731
    assert fill_by_weight({"a": 3, "b": 1}, 4, ["w"], counts, fill, step=1) == {"a": 3, "b": 1}
    assert order == ["a", "b", "a", "a"]
