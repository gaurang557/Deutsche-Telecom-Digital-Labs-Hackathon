"""Deterministic draft repair: family correction and the read-only-plan rejection.

The rejection covered here exists because of a live failure that was worse than
any crash: asked to read a document and update a slide, the planner returned two
reads, both succeeded, and the run was reported as fully completed while the
slide was never touched.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.planning.capabilities import (
    detect_mutation_intent,
    find_invalid_reference_group,
    find_positional_search_text,
    plan_omits_required_mutation,
)
from app.planning.exceptions import InvalidPlannerResponseError
from app.planning.plan_repair import (
    RecoverablePlanError,
    correct_action_families,
    find_advisory_problems,
    find_recoverable_problems,
)
from app.planning.planner import (
    MAX_PLANNING_ATTEMPTS,
    MAX_SEMANTIC_REPAIR_ATTEMPTS,
    OllamaPlanner,
)
from app.schemas import DraftPlan, StructuredActionType, TaskRequest
from app.structured_actions import (
    MODIFYING_STRUCTURED_ACTIONS,
    PERMANENTLY_DENIED_ACTIONS,
    PLANNER_VISIBLE_ACTION_TYPES,
    READ_ONLY_STRUCTURED_ACTIONS,
    action_mutates,
    canonical_family_correction,
)


def _action(step_key: str, action_type: str, target: str, **parameters: object) -> dict:
    return {
        "step_key": step_key,
        "type": action_type,
        "target": target,
        "description": "Do the thing.",
        "parameters": parameters,
        "depends_on": [],
        "expected_result": {"ok": True},
    }


def _plan(*actions: dict) -> dict:
    return {"summary": "I'll take care of that for you.", "actions": list(actions)}


def _draft(*actions: dict) -> DraftPlan:
    return DraftPlan.model_validate(_plan(*actions))


_READ_DOC = _action("read_it", "document.read_text", "Desktop/notes.docx", max_chars=2000)
_WRITE_CELL = _action(
    "write_it", "spreadsheet.write_cell", "Desktop/book.xlsx", cell="B2", value=5
)
_REPLACE_SLIDE = _action(
    "edit_it",
    "presentation.replace_text",
    "Desktop/deck.pptx",
    find="old wording",
    replace="new wording",
    save_as="Desktop/deck_updated.pptx",
)


class _ScriptedPlanner(OllamaPlanner):
    """An OllamaPlanner whose transport is replaced by a fixed script."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(Settings())
        self._replies = replies
        self.prompts: list[list[dict[str, str]]] = []

    def _chat(self, messages, minimum_actions=1):  # type: ignore[override]
        self.prompts.append([dict(message) for message in messages])
        return self._replies[len(self.prompts) - 1]


# ── the canonical mutation predicate ────────────────────────────────────────
def test_action_mutates_agrees_with_the_canonical_registry() -> None:
    for action_type in MODIFYING_STRUCTURED_ACTIONS:
        assert action_mutates(action_type) is True, action_type
    for action_type in READ_ONLY_STRUCTURED_ACTIONS:
        assert action_mutates(action_type) is False, action_type


def test_action_mutates_accepts_enum_members_as_well_as_strings() -> None:
    assert action_mutates(StructuredActionType.SPREADSHEET_WRITE_CELL) is True
    assert action_mutates(StructuredActionType.PDF_READ_TEXT) is False
    assert action_mutates("not.a.real.action") is False


# ── extension → action-family correction ────────────────────────────────────
def test_a_pdf_target_on_a_document_action_is_corrected_to_the_pdf_family() -> None:
    assert canonical_family_correction("document.read_text", "Desktop/report.pdf") == (
        "pdf.read_text"
    )


def test_correction_is_applied_to_a_draft_and_reported() -> None:
    draft = _draft(
        _action("read_it", "document.read_text", "Desktop/report.pdf", max_chars=2000),
        _WRITE_CELL,
    )

    corrected, corrections = correct_action_families(draft)

    assert [str(action.type) for action in corrected.actions] == [
        "pdf.read_text",
        "spreadsheet.write_cell",
    ]
    assert len(corrections) == 1
    assert corrections[0].step_key == "read_it"
    assert corrections[0].previous == "document.read_text"
    assert corrections[0].corrected == "pdf.read_text"
    assert "document.read_text -> pdf.read_text" in corrections[0].describe()


def test_a_matching_extension_is_left_alone() -> None:
    draft = _draft(_READ_DOC)

    corrected, corrections = correct_action_families(draft)

    assert corrections == []
    assert corrected is draft


def test_the_extension_agnostic_file_family_is_never_rewritten() -> None:
    # file.* deliberately works on any extension, so there is nothing to correct.
    assert canonical_family_correction("file.read_text", "Desktop/report.pdf") is None
    assert canonical_family_correction("file.copy", "Desktop/book.xlsx") is None


def test_an_unmapped_extension_is_not_guessed_at() -> None:
    assert canonical_family_correction("document.read_text", "Desktop/notes.txt") is None
    assert canonical_family_correction("document.read_text", "Desktop/noextension") is None


def test_a_verb_with_no_equivalent_in_the_target_family_is_not_invented() -> None:
    # There is no pdf.replace_text — a PDF cannot be edited by this build — so
    # the correction must decline rather than fabricate one.
    assert canonical_family_correction("document.replace_text", "Desktop/report.pdf") is None


def test_no_correction_ever_escalates_privilege() -> None:
    """Every available correction must stay as narrow as the action it replaces."""
    targets = ["a.pdf", "a.xlsx", "a.docx", "a.pptx"]
    for action_type in sorted(READ_ONLY_STRUCTURED_ACTIONS | MODIFYING_STRUCTURED_ACTIONS):
        for target in targets:
            corrected = canonical_family_correction(action_type, target)
            if corrected is None:
                continue
            # A read may never become a write.
            if not action_mutates(action_type):
                assert not action_mutates(corrected), (action_type, corrected)
            # A correction may never reach an action the planner cannot propose,
            # and never a permanently denied one.
            assert corrected in PLANNER_VISIBLE_ACTION_TYPES, (action_type, corrected)
            assert corrected not in PERMANENTLY_DENIED_ACTIONS, (action_type, corrected)


def test_corrections_are_discarded_when_the_new_family_rejects_the_parameters() -> None:
    # `slide` is meaningful to presentation.read_text and meaningless to
    # document.read_text, so rather than dropping a parameter the correction
    # backs out entirely and leaves the problem to the repair loop.
    draft = _draft(_action("look", "presentation.read_text", "Desktop/notes.docx", slide=3))

    corrected, corrections = correct_action_families(draft)

    assert corrections == []
    assert corrected is draft
    assert find_recoverable_problems(corrected, "read the notes") != []


# ── mutation intent, read from the user's words only ────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "update slide 3 of the deck",
        "fill in the revenue column",
        "put the total in the summary row",
        "replace the placeholder with the real figure",
        "move the reports into a subfolder",
        "save the summary next to it",
    ],
)
def test_mutation_intent_is_detected_in_change_requests(text: str) -> None:
    assert detect_mutation_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "read the recommendation from the report",
        "what is the total revenue",
        "summarize the quarterly figures",
        "show me the newest file",
        "list everything in that folder",
        "find the section about pricing",
        "open the deck",
    ],
)
def test_genuinely_read_only_requests_are_not_treated_as_mutations(text: str) -> None:
    assert detect_mutation_intent(text) is False


def test_a_read_only_plan_for_a_change_request_is_rejected() -> None:
    problem = plan_omits_required_mutation(
        "read the recommendation and update slide 3",
        [StructuredActionType.DOCUMENT_READ_TEXT, StructuredActionType.PRESENTATION_READ_TEXT],
    )

    assert problem is not None
    assert "only reads" in problem


def test_a_plan_containing_a_change_satisfies_the_check() -> None:
    assert (
        plan_omits_required_mutation(
            "read the recommendation and update slide 3",
            [
                StructuredActionType.DOCUMENT_READ_TEXT,
                StructuredActionType.PRESENTATION_REPLACE_TEXT,
            ],
        )
        is None
    )


def test_a_read_only_plan_for_a_read_only_request_is_accepted() -> None:
    assert (
        plan_omits_required_mutation(
            "summarize the quarterly figures",
            [StructuredActionType.PDF_READ_TEXT],
        )
        is None
    )


# ── the planner's repair loop ───────────────────────────────────────────────
def test_the_live_two_read_plan_for_an_update_request_is_repaired_and_succeeds() -> None:
    """The exact live defect: two reads for an update request, reported as done."""
    read_only = json.dumps(
        _plan(
            _READ_DOC,
            _action("look", "presentation.read_text", "Desktop/deck.pptx", slide=3),
        )
    )
    repaired = json.dumps(_plan(_READ_DOC, _REPLACE_SLIDE))
    planner = _ScriptedPlanner([read_only, repaired])

    draft = planner._create_draft_sync(
        TaskRequest(text="Read the recommendation from notes.docx and update slide 3 of deck.pptx")
    )

    assert [str(action.type) for action in draft.actions] == [
        "document.read_text",
        "presentation.replace_text",
    ]
    assert any(action_mutates(action.type) for action in draft.actions)
    assert len(planner.prompts) == 2
    repair_turn = planner.prompts[1][-1]
    assert repair_turn["role"] == "user"
    assert "only reads" in repair_turn["content"]


def test_a_persistently_read_only_plan_fails_closed_instead_of_reporting_success() -> None:
    read_only = json.dumps(
        _plan(
            _READ_DOC,
            _action("look", "presentation.read_text", "Desktop/deck.pptx", slide=3),
        )
    )
    planner = _ScriptedPlanner([read_only] * (MAX_PLANNING_ATTEMPTS + 2))

    with pytest.raises(InvalidPlannerResponseError):
        planner._create_draft_sync(
            TaskRequest(text="update slide 3 of deck.pptx from notes.docx")
        )

    # Bounded by BOTH caps, and never more than the overall attempt budget.
    assert len(planner.prompts) <= MAX_PLANNING_ATTEMPTS
    assert len(planner.prompts) <= MAX_SEMANTIC_REPAIR_ATTEMPTS + 1


def test_a_mismatched_file_type_is_corrected_without_costing_a_repair_turn() -> None:
    # This is the failure that used to end the task outright:
    # "document.read_text needs a .docx path but the planner proposed '...pdf'".
    mismatched = json.dumps(
        _plan(
            _action("read_it", "document.read_text", "Desktop/report.pdf", max_chars=2000),
            _WRITE_CELL,
        )
    )
    planner = _ScriptedPlanner([mismatched])

    draft = planner._create_draft_sync(
        TaskRequest(text="put the total from report.pdf into book.xlsx")
    )

    assert [str(action.type) for action in draft.actions] == [
        "pdf.read_text",
        "spreadsheet.write_cell",
    ]
    assert len(planner.prompts) == 1


def test_an_uncorrectable_mismatch_is_sent_back_to_the_planner() -> None:
    # .docx on a spreadsheet action: spreadsheet has no read_text equivalent, so
    # there is no unambiguous correction and the planner must fix it.
    unfixable = json.dumps(
        _plan(_action("look", "spreadsheet.read_range", "Desktop/notes.docx", range="A1:D30"))
    )
    good = json.dumps(_plan(_READ_DOC, _WRITE_CELL))
    planner = _ScriptedPlanner([unfixable, good])

    draft = planner._create_draft_sync(
        TaskRequest(text="put the total from notes.docx into book.xlsx")
    )

    assert len(draft.actions) == 2
    assert len(planner.prompts) == 2
    assert "spreadsheet.read_range" in planner.prompts[1][-1]["content"]


def test_a_recoverable_plan_error_is_a_value_error_so_the_existing_loop_repairs_it() -> None:
    assert issubclass(RecoverablePlanError, ValueError)


# ── a $ref must be able to produce the group it asks for ─────────────────────
def _ref(regex: str, group: object = 1) -> dict:
    return {"$ref": "earlier.evidence.text", "regex": regex, "group": group}


def test_a_regex_without_the_requested_group_is_reported() -> None:
    """The live 500: no parentheses, asking for group 1."""
    problem = find_invalid_reference_group({"value": _ref(r"Revenue:\s*[0-9.]+")})

    assert problem is not None
    assert "capture group" in problem


def test_a_group_beyond_the_defined_ones_is_reported() -> None:
    problem = find_invalid_reference_group({"value": _ref(r"(\d+)-(\d+)", 3)})

    assert problem is not None
    assert "capture group" in problem


def test_an_uncompilable_regex_is_reported() -> None:
    problem = find_invalid_reference_group({"value": _ref(r"(unclosed")})

    assert problem is not None
    assert "not a valid pattern" in problem


def test_an_unknown_named_group_is_reported() -> None:
    problem = find_invalid_reference_group({"value": _ref(r"(?P<here>\d+)", "elsewhere")})

    assert problem is not None
    assert "no group named" in problem


@pytest.mark.parametrize(
    "reference",
    [
        _ref(r"Revenue:\s*([0-9.]+)"),  # the canonical shape
        _ref(r"(\d+)-(\d+)", 2),  # a later group that does exist
        _ref(r"Revenue:\s*[0-9.]+", 0),  # group 0 is the whole match, always valid
        _ref(r"(?P<value>\d+)", "value"),  # a named group that does exist
        {"$ref": "earlier.evidence.text"},  # no regex at all
    ],
)
def test_a_usable_reference_is_not_reported(reference: dict) -> None:
    assert find_invalid_reference_group({"value": reference}) is None


def test_parameters_without_any_reference_are_not_reported() -> None:
    assert find_invalid_reference_group({"cell": "B2", "value": 27.4}) is None
    assert find_invalid_reference_group({}) is None


def test_a_reference_nested_in_a_list_is_still_checked() -> None:
    problem = find_invalid_reference_group({"values": [{"inner": _ref(r"no groups")}]})

    assert problem is not None
    assert "capture group" in problem


# ── a replace step must search for content, not for a position ───────────────
@pytest.mark.parametrize(
    "find",
    ["slide 3", "Slide 3", "  slide  3  ", "the slide 3", "page 2", "slide #4", "row 7"],
)
def test_a_bare_positional_reference_is_not_accepted_as_search_text(find: str) -> None:
    problem = find_positional_search_text("presentation.replace_text", {"find": find})

    assert problem is not None
    assert "where to look" in problem


@pytest.mark.parametrize(
    "find",
    [
        "old wording",
        "slide 3 of the annual review",
        "Revenue for slide 3 was 12",
        "Recommendation: expand the northern region",
        "slide three",
        "3",
    ],
)
def test_real_wording_is_left_alone_even_when_it_mentions_a_position(find: str) -> None:
    assert find_positional_search_text("presentation.replace_text", {"find": find}) is None


def test_the_positional_check_only_looks_at_replace_actions() -> None:
    # A read step's parameters are none of this check's business.
    assert find_positional_search_text("presentation.read_text", {"find": "slide 3"}) is None
    assert find_positional_search_text("spreadsheet.write_cell", {"find": "slide 3"}) is None


def test_a_find_bound_to_an_earlier_step_is_not_mistaken_for_a_position() -> None:
    # A $ref is a dict, not a string, so there is nothing to pattern-match.
    reference = {
        "$ref": "read_slide.evidence.text",
        "regex": r"(.+)",
        "group": 1,
        "coerce": "string",
    }
    assert (
        find_positional_search_text("presentation.replace_text", {"find": reference}) is None
    )


def test_a_positional_find_is_reported_as_an_advisory_not_a_rejection() -> None:
    """CONTRACT CHANGED DELIBERATELY: this no longer stops the plan.

    Rejecting it and asking for a repair was the original behaviour and it made a
    live run strictly worse: the local model cannot supply the alternative (the
    deck's placeholder token, unknowable without reading the deck first), so every
    attempt burned budget and the task ended as an opaque "could not produce a
    valid action plan" instead of running at all.

    Allowed through, the plan executes and fails at the replace step with
    `Text not found in presentation: 'slide 3'`, which says exactly what is wrong.
    Nothing unsafe is permitted by this: a replace that matches nothing writes
    nothing, and the mutation-completeness check still requires the write step to
    be PRESENT, so the silent-success defect stays fixed.
    """
    draft = DraftPlan.model_validate(
        _plan(
            _READ_DOC,
            _action(
                "edit_it",
                "presentation.replace_text",
                "Desktop/deck.pptx",
                find="slide 3",
                replace="the recommendation",
            ),
        )
    )
    request = "read the recommendation from notes.docx and update slide 3 of deck.pptx"

    # Not a recoverable problem, so the repair loop is never entered...
    assert find_recoverable_problems(draft, request) == []
    # ...but it is still recorded, so the operator can see it in the console.
    advisories = find_advisory_problems(draft)
    assert len(advisories) == 1
    assert "where to look" in advisories[0]


def test_a_positional_find_now_plans_on_the_first_attempt() -> None:
    positional = json.dumps(
        _plan(
            _READ_DOC,
            _action(
                "edit_it",
                "presentation.replace_text",
                "Desktop/deck.pptx",
                find="slide 3",
                replace="the recommendation",
            ),
        )
    )
    planner = _ScriptedPlanner([positional])

    draft = planner._create_draft_sync(
        TaskRequest(text="read the recommendation from notes.docx and update slide 3 of deck.pptx")
    )

    # One model round-trip, and the plan still contains the write step.
    assert len(planner.prompts) == 1
    assert draft.actions[-1].parameters["find"] == "slide 3"
    assert any(action_mutates(action.type) for action in draft.actions)


def test_a_legitimate_replace_plan_is_accepted_untouched() -> None:
    good = json.dumps(_plan(_READ_DOC, _REPLACE_SLIDE))
    planner = _ScriptedPlanner([good])

    draft = planner._create_draft_sync(
        TaskRequest(text="read the recommendation from notes.docx and update slide 3 of deck.pptx")
    )

    assert draft.actions[-1].parameters["find"] == "old wording"
    assert len(planner.prompts) == 1


def test_a_read_only_plan_still_fails_closed_even_with_a_positional_find() -> None:
    """The check that actually protects the user is untouched by the downgrade.

    A positional `find` is now tolerated, but a plan of pure READS for a request
    that asked for a change is still rejected and still fails closed. That is the
    check that prevents the silent-success defect, and it must not have been
    loosened by making the search-text check advisory.
    """
    read_only = json.dumps(
        _plan(
            _READ_DOC,
            _action("look", "presentation.read_text", "Desktop/deck.pptx", slide=3),
        )
    )
    planner = _ScriptedPlanner([read_only] * (MAX_PLANNING_ATTEMPTS + 2))

    with pytest.raises(InvalidPlannerResponseError):
        planner._create_draft_sync(
            TaskRequest(text="update slide 3 of deck.pptx from notes.docx")
        )

    assert len(planner.prompts) <= MAX_PLANNING_ATTEMPTS
    assert len(planner.prompts) <= MAX_SEMANTIC_REPAIR_ATTEMPTS + 1


def test_the_repair_message_stays_bounded() -> None:
    planner = _ScriptedPlanner(
        [
            json.dumps(_plan(_action("look", "document.read_text", "Desktop/a.docx"))),
            json.dumps(_plan(_READ_DOC, _WRITE_CELL)),
        ]
    )

    planner._create_draft_sync(TaskRequest(text="update a.docx and book.xlsx"))

    repair_turn = planner.prompts[1][-1]["content"]
    # The validation detail is clamped; no file contents are ever echoed back.
    assert len(repair_turn) < 2_500
