export type ApplicationChoice = {id: string; name: string; category: string; description: string};
export type ApplicationResolution = {
  status: 'query' | 'selected' | 'clarification' | 'navigation' | 'unsupported' | 'reset';
  message: string; application_id: string | null; application_name: string | null;
  query_text: string | null; candidates: ApplicationChoice[]; features: string[];
  feature_actions?: {id: string; label: string; text: string}[];
  target: 'admin' | null; tab: 'basic' | 'page' | 'questions' | 'history';
};
export type ManagementLocation = {applicationId?: string; tab?: ApplicationResolution['tab']};
