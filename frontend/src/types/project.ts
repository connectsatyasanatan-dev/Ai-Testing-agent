export interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  owner_id: string;
}

export interface ProjectListResponse {
  items: Project[];
  total: number;
}
