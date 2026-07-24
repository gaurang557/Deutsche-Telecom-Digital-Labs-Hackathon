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
from app.planning.capabilities import detect_mutation_intent, plan_omits_required_mutation
from app.planning.exceptions import InvalidPlannerResponseError
from app.planning.plan_repair import (
    RecoverablePlanError,
    correct_action_families,
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
