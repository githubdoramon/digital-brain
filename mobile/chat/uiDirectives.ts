export type UiDirectiveOption = {
  id: string;
  label: string;
};

export type UiDirectiveLink = {
  label: string;
  url: string;
};

export type UiDirectiveField = {
  id: string;
  kind: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  options?: UiDirectiveOption[];
};

export type UiDirectiveBlock = {
  id: string;
  type: 'clarification_form' | 'choice_buttons' | 'info_card';
  title?: string;
  description?: string;
  submit_label?: string;
  action_id?: string;
  fields?: UiDirectiveField[];
  options?: UiDirectiveOption[];
  links?: UiDirectiveLink[];
  body?: string;
};

export type UiDirectives = {
  version: string;
  fallback_text: string;
  blocks: UiDirectiveBlock[];
};

export type UiSubmissionInput = {
  block_id?: string;
  action_id?: string;
  values?: Record<string, unknown>;
  text_fallback?: string;
};

