// Hand-written to mirror apps/api/schemas.py.

export type RequesterType = 'customer' | 'internal' | 'partner' | 'unknown';
export const REQUESTER_TYPES: RequesterType[] = ['customer', 'internal', 'partner', 'unknown'];

export interface User {
  id: number;
  name: string;
  role: string;
}

export interface Tag {
  id: number;
  board_id: number;
  name: string;
  color: string;
}

export interface Card {
  id: number;
  board_id: number;
  column_id: number;
  position: number;
  title: string;
  description: string;
  requester_type: RequesterType;
  progress: number;
  priority_score: number | null;
  version: number;
  created_by_id: number | null;
  created_at: string;
  updated_at: string;
  tags: Tag[];
  vote_count: number;
  comment_count: number;
  duplicate_count: number;
  related_count: number;
  pending_draft_count: number;
  pending_suggestion_count: number;
  needs_review: boolean;
}

export interface Column {
  id: number;
  board_id: number;
  name: string;
  position: number;
  wip_limit: number | null;
}

export interface BoardColumn extends Column {
  cards: Card[];
}

export interface Board {
  id: number;
  name: string;
  description: string;
  created_at: string;
}

export interface BoardDetail extends Board {
  columns: BoardColumn[];
}

export interface Comment {
  id: number;
  card_id: number;
  author: User;
  body: string;
  created_at: string;
}

export interface Feedback {
  id: number;
  card_id: number;
  source: string;
  quote: string;
  customer_ref: string | null;
  created_at: string;
}

export interface CardLink {
  card_id: number;
  title: string;
  kind: 'related' | 'duplicate';
  status: 'suggested' | 'confirmed' | 'rejected';
}

export interface ProblemRef {
  id: number;
  title: string;
  status: 'suggested' | 'confirmed';
}

export interface CardDetail extends Card {
  comments: Comment[];
  feedback: Feedback[];
  links: CardLink[];
  problems: ProblemRef[];
  latest_score: unknown | null;
}

export interface VoteSummary {
  card_id: number;
  vote_count: number;
  voted: boolean;
}

export interface CardCreateInput {
  board_id: number;
  column_id?: number;
  title: string;
  description?: string;
  requester_type?: RequesterType;
  tag_ids?: number[];
}

export interface CardUpdateInput {
  title?: string;
  description?: string;
  progress?: number;
  tag_ids?: number[];
  version?: number;
}

export interface ErrorDetail {
  field: string | null;
  message: string;
}

// ---- AI workflow ------------------------------------------------------------------------------
export const STEP_LABELS: Record<string, string> = {
  validate_intake: 'Validate intake',
  analyze_need: 'Analyze need',
  find_related: 'Find related',
  group_problem: 'Group problem',
  score: 'Score',
  suggest_tags: 'Suggest tags',
  decision_brief: 'Decision brief',
  draft_comms: 'Draft message',
  human_checkpoint: 'Waiting for you',
  needs_info: 'Ask for details',
  merge_proposal: 'Merge proposal',
};
export const TRIAGE_STEPS = [
  'validate_intake',
  'analyze_need',
  'find_related',
  'group_problem',
  'score',
  'suggest_tags',
  'decision_brief',
  'draft_comms',
] as const;

export type RunStatus = 'running' | 'awaiting_human' | 'succeeded' | 'failed';

export interface RunStep {
  name: string;
  status: string;
  requested: boolean;
  route?: boolean;
  started_at: string | null;
  finished_at: string | null;
  latency_ms: number | null;
  result: Record<string, unknown> | null;
  error: string | null;
  reason: string | null;
}

export interface PendingAction {
  kind: 'confirm_duplicate' | 'confirm_actionable';
  run_id: number;
  card_id: number;
  payload: Record<string, any>;
  error?: string;
}

export interface Run {
  run_id: number;
  card_id: number | null;
  status: RunStatus;
  outcome: string | null;
  steps: RunStep[];
  progress: { done: number; total: number };
  pending_action: PendingAction | null;
  human_decisions: Record<string, unknown>[];
  errors: { step: string; error: string }[];
  created_at: string;
  finished_at: string | null;
}

export interface RunSummary {
  run_id: number;
  card_id: number | null;
  status: RunStatus;
  outcome: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface RunAccepted {
  run_id: number;
  card_id: number;
  status: string;
  steps_requested: string[] | null;
  card_created: boolean;
}

export interface TriageCardInput {
  board_id: number;
  title: string;
  description?: string;
  requester_type?: RequesterType;
  column_id?: number;
  tag_ids?: number[];
}

export type SuggestionType =
  | 'duplicate'
  | 'related'
  | 'problem'
  | 'new_problem'
  | 'tag'
  | 'score'
  | 'brief'
  | 'comms'
  | 'comment'
  | 'field_edit'
  | 'analysis';

export interface Suggestion {
  id: string;
  type: SuggestionType;
  status: string;
  confidence: number | null;
  rationale: string | null;
  data: Record<string, any>;
  created_at: string;
  run_id: number | null;
  editable: string[];
}

export interface Decision {
  suggestion_id: string;
  status: string;
  message: string;
  effects: Record<string, any>;
  audit_id: number | null;
}

export interface AuditEntry {
  id: number;
  card_id: number | null;
  actor_id: number | null;
  action: string;
  detail: Record<string, any>;
  created_at: string;
}

export interface Metrics {
  time_to_first_decision: { cards: number; mean_minutes: number | null; median_minutes: number | null; slowest_minutes: number | null };
  acceptance: {
    accepted: number;
    rejected: number;
    decided: number;
    rate: number | null;
    accepted_without_changes_rate: number | null;
    by_type: Record<string, { accepted: number; rejected: number; rate: number | null }>;
  };
  duplicates: { merged: number; votes_moved: number; duplicates_confirmed_as_suggested: number; duplicates_rejected: number };
  score_overrides: { overridden: number; kept: number; rate: number | null; scores_rejected: number };
  demo_entries: number;
  total_decisions: number;
  definitions: Record<string, string>;
  baseline: { is_assumption: boolean; manual_triage_minutes: number; description: string; comparable: boolean };
}
