export type ApplicationChoice = {id: string; name: string; category: string; description: string};
export type ApplicationResolution = {
  status: 'query' | 'selected' | 'clarification' | 'navigation' | 'unsupported' | 'reset';
  message: string; application_id: string | null; application_name: string | null;
  query_text: string | null; candidates: ApplicationChoice[]; features: string[];
  feature_actions?: {id: string; label: string; text: string}[];
  target: 'admin' | null; tab: 'basic' | 'page' | 'questions' | 'history';
  construction_subject?: string | null;
  type_id?: string | null;
  runtime_action?: 'fill' | 'records' | null;
};
export type ManagementLocation = {applicationId?: string; tab?: ApplicationResolution['tab']; taskId?: string};
