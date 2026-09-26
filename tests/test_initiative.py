"""
Tests for core/initiative.py — the mood engine and the activity director.

What is actually being protected here:

  * the mood is a *quantity*, not a flag — it moves for a reason, it decays, and
    it does not flicker between two close scores;
  * "do nothing" is a reachable, ordinary outcome rather than an accident;
  * the same kind of thing is never chosen twice in a row, because that is the
    specific failure the whole module exists to prevent;
  * a restored session is not wiped by the first clock tick (a regression that
    would have made the on-disk state decorative);
  * everything that grows is capped.

Nothing here touches the real config: STATE_PATH is redirected to a temp dir,
and no Director writes outside it.
"""
import json
import random
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

from core import initiative as I


class _Isolated(unittest.TestCase):
    """Redirects persistence into a temp dir for every test in the class."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "initiative.json"
        self._patch = mock.patch.object(I, "STATE_PATH", self.state)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def director(self, seed=7, **kw):
        d = I.Director(rng=random.Random(seed), **kw)
        d._loaded = True          # skip disk on construction; tests set state
        return d


class MoodTest(unittest.TestCase):
    """Six states, held as scores. The brief's palette, literally."""

    def test_all_six_moods_exist_and_are_described(self):
        self.assertEqual(len(I.MOODS), 6)
        for mood in ("CURIOUS", "BORED", "HAPPY", "FOCUSED", "RELAXED", "EXCITED"):
            self.assertIn(mood, I.MOODS)
            info = I.MOOD_INFO[mood]
            for field in ("label", "blurb", "tone", "icon", "wants"):
                self.assertTrue(info.get(field), f"{mood} missing {field}")

    def test_every_mood_wants_real_activities(self):
        for mood, info in I.MOOD_INFO.items():
            for key in info["wants"]:
                self.assertIn(key, I.ACTIVITY_BY_KEY,
                              f"{mood} reaches for unknown activity {key!r}")

    def test_resting_mood_is_curiosity_not_boredom(self):
        m = I.Mood()
        self.assertEqual(m.name, "CURIOUS")
        self.assertGreater(I.BASELINE["CURIOUS"], I.BASELINE["BORED"])

    def test_discovery_raises_curiosity_and_suppresses_boredom(self):
        m = I.Mood()
        m.scores["BORED"] = 0.7
        m.scores["CURIOUS"] = 0.2
        m._settle()                       # begin from BORED, honestly
        self.assertEqual(m.name, "BORED")
        changed = m.observe("discovered", 1.0, now=time.time())
        self.assertLess(m.scores["BORED"], 0.7)
        self.assertGreater(m.scores["CURIOUS"], 0.2)
        self.assertTrue(changed)
        self.assertEqual(m.name, "CURIOUS")

    def test_repeated_stalls_reach_bored(self):
        m = I.Mood()
        for _ in range(6):
            m.observe("stalled", 1.0)
            m.observe("repeated", 1.0)
        self.assertEqual(m.name, "BORED")

    def test_enjoyment_reaches_happy_or_relaxed(self):
        m = I.Mood()
        for _ in range(4):
            m.observe("enjoyed", 1.0)
        self.assertIn(m.name, ("HAPPY", "RELAXED"))

    def test_direction_is_sharp_when_the_user_is_driving(self):
        """A real instruction has to win against a good mood — but not through
        one event, because the suppression scale means a strongly-held mood
        resists being erased by a single nudge."""
        m = I.Mood()
        for _ in range(3):
            m.observe("enjoyed", 1.0)
        self.assertIn(m.name, ("HAPPY", "RELAXED"))
        for _ in range(3):
            m.observe("directed", 1.0)
        self.assertEqual(m.name, "FOCUSED")

    def test_scores_stay_inside_the_unit_interval(self):
        m = I.Mood()
        for _ in range(40):
            m.observe("discovered", 4.0)
            m.observe("stalled", 4.0)
        for mood, score in m.scores.items():
            self.assertGreaterEqual(score, 0.0, mood)
            self.assertLessEqual(score, 1.0, mood)

    def test_a_small_nudge_does_not_flip_the_mood(self):
        """Hysteresis: two scores this close must not swap on a rounding error."""
        m = I.Mood()
        m.name = "CURIOUS"
        m.scores = {"CURIOUS": 0.40, "FOCUSED": 0.36, "EXCITED": 0.1,
                    "HAPPY": 0.1, "RELAXED": 0.1, "BORED": 0.1}
        self.assertFalse(m.observe("landed", 0.1))
        self.assertEqual(m.name, "CURIOUS")

    def test_a_large_lead_does_take_over(self):
        m = I.Mood()
        m.name = "CURIOUS"
        m.scores = {"CURIOUS": 0.15, "FOCUSED": 0.5, "EXCITED": 0.1,
                    "HAPPY": 0.1, "RELAXED": 0.1, "BORED": 0.1}
        self.assertTrue(m.observe("landed", 0.2))
        self.assertEqual(m.name, "FOCUSED")

    def test_decay_pulls_every_score_toward_its_baseline(self):
        m = I.Mood()
        m.scores["HAPPY"] = 1.0
        m.scores["BORED"] = 0.9
        m.tick(now=m._accounted + 6 * 3600.0)
        self.assertLess(m.scores["HAPPY"], 0.5)
        self.assertLess(m.scores["BORED"], 0.5)

    def test_decay_is_step_independent(self):
        """One tick of an hour equals sixty ticks of a minute — otherwise the
        mood ages at the speed of the loop rather than the speed of the clock."""
        a = I.Mood()
        b = I.Mood()
        a.scores["EXCITED"] = 1.0
        b.scores["EXCITED"] = 1.0
        a.tick(now=a._accounted + 3600.0)
        for _ in range(60):
            b.tick(now=b._accounted + 60.0)
        self.assertAlmostEqual(a.scores["EXCITED"], b.scores["EXCITED"], places=6)

    def test_a_restored_mood_survives_its_first_tick(self):
        """Regression: with a zero clock origin the first tick sees decades of
        elapsed time and erases the restored mood."""
        m = I.Mood()
        m.name = "EXCITED"
        m.scores = {"EXCITED": 0.9, "CURIOUS": 0.3, "FOCUSED": 0.1,
                    "HAPPY": 0.1, "RELAXED": 0.1, "BORED": 0.1}
        m.tick()
        self.assertEqual(m.name, "EXCITED")
        self.assertGreater(m.scores["EXCITED"], 0.8)

    def test_intensity_is_a_usable_fraction(self):
        m = I.Mood()
        for _ in range(3):
            m.observe("discovered", 2.0)
        self.assertGreaterEqual(m.intensity, 0.15)
        self.assertLessEqual(m.intensity, 1.0)

    def test_info_is_ui_ready(self):
        info = I.Mood().info()
        for field in ("name", "label", "icon", "tone", "blurb", "intensity", "since"):
            self.assertIn(field, info)
        self.assertNotIn("CURIOUS", ("bored",))     # tone is a css token, not a shout
        self.assertEqual(info["tone"], info["tone"].lower())

    def test_unknown_event_and_junk_weight_are_ignored(self):
        """An unrecognised event changes nothing; an unrecognised *weight* falls
        back to 1.0 rather than raising — a mood must not be able to break a
        turn because a caller passed a string."""
        m = I.Mood()
        before = dict(m.scores)
        self.assertFalse(m.observe("", 1.0))
        self.assertFalse(m.observe("nonsense", 1.0))
        self.assertEqual(before, m.scores)
        m.observe("discovered", "not a number")
        self.assertGreater(m.scores["CURIOUS"], before["CURIOUS"])


class CatalogueTest(unittest.TestCase):
    """The list of things worth doing, and the rules inside its missions."""

    def test_keys_are_unique(self):
        keys = [a.key for a in I.CATALOGUE]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_activity_is_presentable(self):
        for act in I.CATALOGUE:
            self.assertTrue(act.label.endswith("…"), act.key)
            self.assertGreater(len(act.mission), 80, act.key)
            self.assertGreater(act.weight, 0.0, act.key)
            self.assertGreater(act.cooldown, 0.0, act.key)

    def test_the_brief_s_activities_are_all_represented(self):
        """Research, read, learn, YouTube/video, music, funny, news, GitHub,
        websites, files, apps, unfinished work, useful preparation."""
        keys = set(I.ACTIVITY_BY_KEY)
        for wanted in ("research", "read", "learn", "video", "music", "funny",
                       "news", "github", "explore", "tidy", "apps", "followup",
                       "useful"):
            self.assertIn(wanted, keys)

    def test_every_mission_reports_and_respects_the_boundary(self):
        """The rules live in one place and are folded into every assembled
        mission, so no row can forget to say how to report back or what is
        forbidden."""
        d = I.Director(rng=random.Random(1))     # pure: _mission touches no disk
        for act in I.CATALOGUE:
            mission = d._mission(act)
            self.assertIn("ONE short sentence", mission, act.key)
            self.assertIn("Never, unasked", mission, act.key)

    def test_media_activities_are_in_a_different_family_from_work(self):
        media = {a.family for a in I.CATALOGUE if a.key in ("music", "video", "funny")}
        work = {a.family for a in I.CATALOGUE if a.key in ("followup", "useful")}
        self.assertEqual(media, {"media"})
        self.assertTrue(work.isdisjoint(media))

    def test_tidy_is_move_only(self):
        tidy = I.ACTIVITY_BY_KEY["tidy"]
        self.assertIn("never delete", tidy.mission.lower())


class NextMoveTest(_Isolated):
    """What it decides to do — and, just as importantly, when it decides to do
    nothing at all."""

    def test_stays_quiet_while_the_user_may_still_be_around(self):
        d = self.director()
        self.assertIsNone(d.next_move(idle_seconds=5.0))
        self.assertIsNone(d.next_move(idle_seconds=d.min_idle - 1))

    def test_proposes_a_concrete_mission_after_real_silence(self):
        d = self.director()
        move = d.next_move(idle_seconds=d.min_idle + 1)
        self.assertIsNotNone(move)
        self.assertIn(move.key, I.ACTIVITY_BY_KEY)
        self.assertTrue(move.label.endswith("…"))
        self.assertIn("ONE short sentence", move.mission)
        self.assertTrue(move.reason)

    def test_nothing_worth_doing_is_an_honest_outcome(self):
        """With no row clearing the bar, the director must not invent busywork."""
        d = self.director()
        with mock.patch.object(I, "MIN_SCORE", 99.0):
            self.assertIsNone(d.next_move(idle_seconds=10_000.0))
        # ...and the quiet count is what proves it *chose* to be quiet.
        self.assertGreaterEqual(d.stats["quiet"], 1)

    def test_quiet_can_be_overridden_when_the_caller_needs_a_suggestion(self):
        d = self.director()
        with mock.patch.object(I, "MIN_SCORE", 99.0):
            move = d.next_move(idle_seconds=10_000.0, allow_quiet=False)
        self.assertIsNotNone(move)

    def test_never_the_same_activity_twice_in_a_row(self):
        """The headline invariant: a long session must not become the same three
        actions. Each accepted move is recorded, exactly as the loop does.

        `allow_quiet=False` is the caller insisting on a suggestion — the case
        where a repeat would be most tempting and least forgivable."""
        d = self.director()
        seen = []
        for _ in range(12):
            move = d.next_move(idle_seconds=600.0, allow_quiet=False)
            self.assertIsNotNone(move)
            seen.append(move.key)
            d.finish("landed", key=move.key)
        self.assertEqual(len(seen), len(set(seen)), f"repeated an activity: {seen}")
        for prev, cur in zip(seen, seen[1:]):
            self.assertNotEqual(prev, cur)

    def test_when_everything_is_resting_it_takes_the_stalest_idea(self):
        d = self.director()
        for act in I.CATALOGUE:
            d.finish("landed", key=act.key)
        move = d.next_move(idle_seconds=600.0, allow_quiet=False)
        self.assertIsNotNone(move)
        self.assertIn(move.key, I.ACTIVITY_BY_KEY)

    def test_boredom_changes_the_kind_of_thing_it_does(self):
        """Bored + the same family as last time must push it elsewhere."""
        d = self.director(seed=3)
        d.mood.name = "BORED"
        d.mood.scores = {"BORED": 0.9, "CURIOUS": 0.2, "FOCUSED": 0.1,
                         "EXCITED": 0.1, "HAPPY": 0.1, "RELAXED": 0.1}
        d._last_family = "knowledge"
        ranked = d._rank(hour=14)
        top = I.ACTIVITY_BY_KEY[ranked[0][0]]
        self.assertNotEqual(top.family, "knowledge")

    def test_two_light_moves_send_it_back_to_useful_work(self):
        d = self.director(seed=5)
        d._detour = 2
        ranked = d._rank(hour=14)
        top = I.ACTIVITY_BY_KEY[ranked[0][0]]
        self.assertIn(top.family, ("work", "knowledge"))

    def test_music_is_a_better_idea_in_the_evening_than_at_dawn(self):
        def score_at(hour):
            d = self.director(seed=11)
            d.mood.name = "RELAXED"
            rows = dict((k, s) for k, s, _ in d._rank(hour=hour))
            return rows["music"]

        self.assertGreater(score_at(20), score_at(4))

    def test_news_is_better_in_the_morning(self):
        def score_at(hour):
            d = self.director(seed=11)
            rows = dict((k, s) for k, s, _ in d._rank(hour=hour))
            return rows["news"]

        self.assertGreater(score_at(8), score_at(22))

    def test_an_unfinished_task_promotes_following_up(self):
        without = self.director(seed=2)
        without.mood.name = "CURIOUS"
        with_task = self.director(seed=2)
        with_task.mood.name = "CURIOUS"
        with_task.note_task("finish the quarterly summary")
        a = dict((k, s) for k, s, _ in without._rank(hour=14))["followup"]
        b = dict((k, s) for k, s, _ in with_task._rank(hour=14))["followup"]
        self.assertGreater(b, a)

    def test_the_reason_names_why(self):
        d = self.director(seed=4)
        d.note_task("something half-finished")
        move = d.next_move(idle_seconds=600.0)
        self.assertTrue(move.reason)
        self.assertLess(len(move.reason), 120)

    def test_move_carries_the_mood_and_its_context(self):
        d = self.director(seed=4)
        d.note_discovery("Kubernetes moved to a new release cadence")
        move = d.next_move(idle_seconds=600.0)
        self.assertIn(move.mood, I.MOODS)
        self.assertIn("Context you may use", move.mission)

    def test_the_mission_never_exceeds_a_sane_length(self):
        d = self.director(seed=4)
        for i in range(4):
            d.note_task(f"unfinished thing number {i}")
            d.note_discovery(f"finding {i} about quantisation of local models")
            d.note_discovery(f"another {i} about local model quantisation techniques")
        move = d.next_move(idle_seconds=600.0)
        self.assertLess(len(move.mission), 1600)

    def test_act_is_quiet_when_only_the_mood_could_justify_it(self):
        """A move whose score is exactly at the floor is allowed; below is not."""
        d = self.director()
        with mock.patch.object(I, "MIN_SCORE", 0.75):
            d.mood.scores = {k: 0.01 for k in I.MOODS}
            d.mood.name = "CURIOUS"
            self.assertIsNotNone(d.next_move(idle_seconds=600.0))


class DiscoveryTest(_Isolated):
    """What it learned, kept where the user can see it."""

    def test_a_discovery_is_kept_and_counted(self):
        d = self.director()
        row = d.note_discovery("Rust 2027 edition lands with async drop",
                               source="lobste.rs")
        self.assertEqual(row["source"], "lobste.rs")
        self.assertEqual(d.stats["discoveries"], 1)
        self.assertEqual(len(d.discoveries), 1)

    def test_a_discovery_pulls_a_subject_into_the_interest_pool(self):
        d = self.director()
        d.note_discovery("Redis 8 ships vector search natively")
        self.assertTrue(d.interests)

    def test_empty_and_whitespace_discoveries_are_dropped(self):
        d = self.director()
        self.assertEqual(d.note_discovery("   "), {})
        self.assertEqual(len(d.discoveries), 0)

    def test_discovery_text_is_clamped(self):
        d = self.director()
        row = d.note_discovery("x" * 5000)
        self.assertLessEqual(len(row["text"]), 280)

    def test_the_lists_are_capped(self):
        d = self.director()
        for i in range(I.MAX_DISCOVERIES * 3):
            d.note_discovery(f"finding number {i} about something")
            d.note_task(f"task number {i}")
        self.assertLessEqual(len(d.discoveries), I.MAX_DISCOVERIES)
        self.assertLessEqual(len(d.tasks), I.MAX_TASKS)
        self.assertLessEqual(len(d.interests), I.MAX_INTERESTS)

    def test_a_repeated_task_is_not_stored_twice(self):
        d = self.director()
        d.note_task("finish the quarterly summary")
        d.note_task("finish the quarterly summary")
        self.assertEqual(len(d.tasks), 1)

    def test_forget_clears_what_it_was_up_to(self):
        d = self.director()
        d.note_discovery("something interesting about compilers")
        d.note_task("unfinished work")
        d.finish("landed", key="research")
        d.forget()
        self.assertEqual(len(d.discoveries), 0)
        self.assertEqual(len(d.tasks), 0)
        self.assertEqual(len(d.history), 0)
        self.assertEqual(d.mood.name, "CURIOUS")

    def test_status_is_ui_ready(self):
        d = self.director()
        d.note_discovery("a thing worth keeping about gradients")
        st = d.status()
        for field in ("mood", "activity", "activity_label", "discoveries",
                      "discovery_count", "interests", "tasks", "stats"):
            self.assertIn(field, st)
        self.assertEqual(st["discovery_count"], 1)
        self.assertEqual(st["discoveries"][-1]["text"],
                         "a thing worth keeping about gradients")
        self.assertIn("label", st["mood"])


class OutcomeTest(_Isolated):
    """How a move lands has to change the mood, or the mood is decoration."""

    def test_repeating_the_last_activity_invites_boredom(self):
        d = self.director()
        d.finish("landed", key="github")
        first = d.mood.scores["BORED"]
        d.finish("stalled", key="github")
        self.assertGreater(d.mood.scores["BORED"], first)

    def test_a_landed_action_feeds_focus(self):
        d = self.director()
        before = d.mood.scores["FOCUSED"]
        d.finish("landed", key="tidy")
        self.assertGreater(d.mood.scores["FOCUSED"], before)

    def test_a_stall_is_counted(self):
        d = self.director()
        d.finish("stalled", key="apps")
        self.assertEqual(d.stats["stalled"], 1)

    def test_finishing_without_a_key_uses_the_current_move(self):
        d = self.director()
        move = d.next_move(idle_seconds=600.0)
        d.finish("landed")
        self.assertEqual(d.history[-1]["key"], move.key)
        self.assertIsNone(d.current)

    def test_unknown_outcome_is_treated_as_quiet(self):
        d = self.director()
        d.finish("nonsense", key="read")
        self.assertEqual(d.history[-1]["outcome"], "nonsense")
        self.assertEqual(d.stats["landed"], 0)
        self.assertEqual(d.stats["stalled"], 0)


class PersistenceTest(_Isolated):
    """A session must outlive the process, and a corrupt file must not stop it."""

    def test_round_trip(self):
        d = self.director()
        d.note_discovery("Zig's new build system is worth a look")
        d.note_task("finish the quarterly summary")
        # A consistent score map, not just a renamed winner: setting the name
        # alone would be a lie the next settle() corrects.
        d.mood.name = "EXCITED"
        d.mood.scores = {"EXCITED": 0.8, "CURIOUS": 0.3, "FOCUSED": 0.1,
                         "HAPPY": 0.1, "RELAXED": 0.1, "BORED": 0.1}
        d.finish("landed", key="research")
        d.save(force=True)

        fresh = I.Director()
        fresh.load()
        self.assertEqual(fresh.mood.name, "EXCITED")
        self.assertAlmostEqual(fresh.mood.scores["EXCITED"], 0.8, places=3)
        self.assertEqual(len(fresh.discoveries), 1)
        self.assertEqual(list(fresh.tasks), ["finish the quarterly summary"])
        self.assertEqual(fresh._last_key, "research")

    def test_a_corrupt_file_is_a_cold_start_not_a_crash(self):
        self.state.write_text("{ not json at all", encoding="utf-8")
        d = I.Director()
        d.load()
        self.assertEqual(d.mood.name, "CURIOUS")
        self.assertEqual(len(d.discoveries), 0)

    def test_a_file_with_wrong_shapes_is_absorbed(self):
        self.state.write_text(json.dumps({
            "mood": "NOT_A_MOOD", "scores": {"CURIOUS": "over nine thousand"},
            "discoveries": [1, 2, {"nope": True}], "tasks": "not a list",
            "history": [{"key": ""}, "junk"], "stats": {"moves": "many"},
            "detour": "several",
        }), encoding="utf-8")
        d = I.Director()
        d.load()
        self.assertIn(d.mood.name, I.MOODS)
        self.assertIsInstance(d.tasks, deque)
        self.assertTrue(all(isinstance(s, float) for s in d.mood.scores.values()))
        self.assertIsInstance(d._detour, int)

    def test_the_file_is_written_atomically_and_capped(self):
        d = self.director()
        for i in range(120):
            d.note_discovery(f"finding {i} about something readable")
        d.save(force=True)
        raw = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertLessEqual(len(raw["discoveries"]), I.MAX_DISCOVERIES)
        self.assertFalse(self.state.with_suffix(".tmp").exists(),
                         "a temp file was left behind")

    def test_saving_an_unchanged_director_is_a_no_op(self):
        d = self.director()
        d.save(force=True)
        self.assertFalse(self.state.exists())     # nothing was ever dirty

    def test_an_unwritable_path_never_raises(self):
        with mock.patch.object(I, "STATE_PATH", Path("/nope/does/not/exist/x.json")):
            d = I.Director()
            d.note_discovery("a finding that cannot be saved")
            d._dirty = True
            d.save(force=True)      # must not raise


class PromptBlockTest(unittest.TestCase):
    """What the autonomous turn is actually told."""

    def test_it_names_the_mood_the_choice_and_the_mission(self):
        move = I.Move(mood="CURIOUS", key="research",
                      label="Researching something new…",
                      mission="Read properly and keep the best thing.",
                      reason="Curious reaches for this", tell=True)
        block = I.prompt_block(move)
        self.assertIn("CURIOUS", block)
        self.assertIn("Researching something new…", block)
        self.assertIn("Read properly and keep the best thing.", block)
        self.assertIn("Curious reaches for this", block)

    def test_being_quiet_is_described_as_a_real_choice(self):
        block = I.prompt_block(None)
        self.assertIn("stay quiet", block.lower())

    def test_it_always_lets_the_model_overrule_it(self):
        for move in (None, I.Move(mood="BORED", key="music",
                                  label="Listening to music…",
                                  mission="Put something on.", reason="fits")):
            block = I.prompt_block(move)
            self.assertIn("Override rule", block)

    def test_with_autonomy_off_it_is_a_suggestion_not_an_instruction(self):
        block = I.prompt_block(None, autonomous=False)
        self.assertIn("do not touch the machine", block.lower())


class ModuleApiTest(_Isolated):
    """The thin module-level surface: it must never raise, whatever happens."""

    def test_status_survives_a_broken_director(self):
        with mock.patch.object(I, "director", side_effect=RuntimeError("boom")):
            st = I.status()
        self.assertIn("mood", st)
        self.assertEqual(st["discoveries"], [])

    def test_finish_and_note_discovery_never_raise(self):
        with mock.patch.object(I, "director", side_effect=RuntimeError("boom")):
            I.finish("landed")               # must not raise
            self.assertEqual(I.note_discovery("x"), {})
            I.note_task("x")
            I.forget()

    def test_the_singleton_is_shared(self):
        first = I.director()
        self.assertIs(first, I.director())

    def test_reset_replaces_it(self):
        first = I.director()
        second = I.reset()
        self.assertIsNot(first, second)
        self.assertIs(second, I.director())


class SubjectTest(unittest.TestCase):
    """The interest pool must read like topics, not like stopword soup."""

    def test_it_picks_out_a_topic(self):
        out = I._subject("Redis 8 ships vector search natively")
        self.assertIn("Redis", out)
        self.assertNotIn("the", out.lower().split())

    def test_nothing_useful_gives_nothing(self):
        self.assertEqual(I._subject(""), "")
        self.assertEqual(I._subject("it is and the of"), "")

    def test_it_is_bounded(self):
        long_text = " ".join(f"Comp{chr(65 + i)}lex" for i in range(40))
        self.assertLessEqual(len(I._subject(long_text).split()), 5)


if __name__ == "__main__":
    unittest.main()
