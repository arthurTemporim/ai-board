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
  status: 'suggested' | 'confirmed';
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
