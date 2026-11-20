import pytest

from youtube_edit_ultra.events import Action, validate_no_overlap
from youtube_edit_ultra.planner import integrity_report, plan_edit


def act(source, peak, provenance, confidence, start=0, end=10):
    return Action(source, start, peak, end, confidence=confidence, provenance=provenance)


def pick_one(*actions, beats=(2.0,)):
    """Plan exactly one 10 s slot and return the chosen source."""
    edits = plan_edit(list(actions), list(beats), 10, {a.source: 60 for a in actions})
    assert len(edits) == 1
    return edits[0].source


@pytest.mark.parametrize('miss', [0.40, 0.45, 2.0])
def test_reviewed_action_beats_vlm_guess_whatever_the_beat_miss(miss):
    # Crossover on main: 1.0 * 3 - miss * 1.4 < 0.8 * 3 once miss > 0.4286 s.
    reviewed = act('reviewed', 2.0 + miss, 'annotation', 1.0)
    guess = act('guess', 2.0, 'heuristic_vlm', 0.8)
    assert pick_one(reviewed, guess) == 'reviewed'


def test_soccernet_prediction_loses_to_reviewed_action():
    reviewed = act('reviewed', 3.0, 'annotation', 1.0)
    soccernet = act('soccernet', 2.0, 'SoccerNet', 0.65)
    assert pick_one(reviewed, soccernet) == 'reviewed'


def test_soccernet_prediction_beats_vlm_guess():
    soccernet = act('soccernet', 2.45, 'SoccerNet', 0.65)
    guess = act('guess', 2.0, 'heuristic_vlm', 0.8)
    assert pick_one(soccernet, guess) == 'soccernet'


def test_vlm_rejected_window_does_not_outrank_unrated_motion_peak():
    # semantic.py rates a window 0.10 when no action is visible; --semantic-limit
    # can leave other motion windows unrated at 0.25. Both are automatic guesses.
    rejected = act('rejected', 2.0, 'heuristic_vlm', 0.10)
    unrated = act('unrated', 2.3, 'heuristic', 0.25)
    assert pick_one(rejected, unrated) == 'unrated'


def test_mixed_provenance_ranks_with_its_weakest_part():
    merged, = validate_no_overlap([act('fight', 2.0, 'annotation', 1.0, 0, 6),
                                   act('fight', 7.0, 'heuristic_vlm', 0.8, 5, 10)])
    assert merged.provenance == 'mixed:annotation+heuristic_vlm'
    reviewed = act('reviewed', 3.0, 'annotation', 0.5)
    assert pick_one(merged, reviewed) == 'reviewed'


def dead_end_actions(guesses=2):
    # Greedy takes the 6 s action first and strands 4 s; recovery must find two 5 s actions.
    return ([Action('long', 0, 3, 6, confidence=1.0, provenance='annotation'),
             Action('r1', 0, 2, 5, confidence=0.5, provenance='annotation'),
             Action('r2', 0, 2, 5, confidence=0.5, provenance='annotation')]
            + [Action(f'g{i}', 0, 2, 5, confidence=0.8, provenance='heuristic_vlm')
               for i in range(guesses)])


@pytest.mark.parametrize('guesses', [2, 130])
def test_recovery_prefers_reviewed_actions(guesses):
    actions = dead_end_actions(guesses)
    edits = plan_edit(actions, [], 10, {a.source: a.end for a in actions})
    assert [e.duration for e in edits] == [5, 5]
    assert {e.provenance for e in edits} == {'annotation'}


@pytest.mark.parametrize('target, expected', [
    (15, [('c', 3, 0.0), ('b', 0, 3.0), ('b', 20, 8.0)]),       # greedy only
    (17, [('b', 20, 0.0), ('a', 10, 7.0), ('a', 0, 13.0)]),     # greedy dead end, recovery
])
@pytest.mark.parametrize('provenance', ['heuristic', 'heuristic_vlm'])
def test_single_tier_plans_are_unchanged(provenance, target, expected):
    # Pinned from main at 6ced710: inside one provenance tier the ranking must not move.
    actions = [Action('a', 0, 1.3, 4, confidence=.25, provenance=provenance),
               Action('a', 10, 12.1, 16, confidence=.25, provenance=provenance),
               Action('b', 0, 2.6, 5, confidence=.6, provenance=provenance),
               Action('b', 20, 21.0, 27, confidence=.3, provenance=provenance),
               Action('c', 3, 4.0, 6, confidence=.8, provenance=provenance)]
    edits = plan_edit(actions, [i * .5 for i in range(80)], target,
                      {'a': 30, 'b': 30, 'c': 30})
    assert [(e.source, e.source_start, e.timeline_start) for e in edits] == expected


def test_report_counts_unreviewed_seconds():
    actions = [act('reviewed', 2.0, 'annotation', 1.0, 0, 6),
               act('guess', 2.0, 'heuristic_vlm', 0.8, 0, 4)]
    edits = plan_edit(actions, [], 10, {'reviewed': 6, 'guess': 4})
    report = integrity_report(edits, actions, 10)
    assert report['selected_seconds_by_provenance_rank'] == {'2': 6.0, '1': 0, '0': 4.0}
    assert report['unreviewed_seconds_selected'] == 4.0
    assert report['filler_seconds'] == 0
    assert report['filled_with_unreviewed_footage'] is True


def test_report_fully_reviewed_run_has_no_unreviewed_seconds():
    actions = [act('reviewed', 2.0, 'annotation', 1.0)]
    report = integrity_report(plan_edit(actions, [], 10, {'reviewed': 10}), actions, 10)
    assert report['unreviewed_seconds_selected'] == 0
    assert report['filler_seconds'] == 0
    assert report['filled_with_unreviewed_footage'] is False


def test_report_counts_filler_as_unreviewed_fill():
    actions = [act('fight', 5.0, 'annotation', 1.0, 0, 8)]
    edits = plan_edit(actions, [], 10, {'fight': 30}, max_filler=3)
    report = integrity_report(edits, actions, 10)
    assert report['filler_seconds'] == 2.0
    assert report['unreviewed_seconds_selected'] == 0
    assert report['filled_with_unreviewed_footage'] is True
