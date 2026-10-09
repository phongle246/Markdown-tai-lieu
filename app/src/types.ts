export interface Chapter {
  key: string; number: number | null; title: string; start: number; end: number; part: string;
  confidence: number; source: string; start_y: number | null; end_y: number | null; issues: string[];
  outline_title?: string;
  state?: ChapterState; interrupted?: boolean; has_markdown?: boolean;
}
export interface ChapterState {
  status: string; pages_done?: number; pages_total?: number; stage?: string; error?: string | null;
  counts?: Record<string, number>; updated?: string; finished?: string;
}
export interface Book {
  title: string; edition: string; authors: string; book_id: string; pages: number;
  source_file: string; source_file_name: string; source_sha256: string; created?: string; has_outline?: boolean;
}
export interface Overview {
  id: string; root: string; book: Book; chapters: Chapter[];
  progress: { done: number; total: number; percent: number };
}
export interface LibraryItem {
  id: string; title: string; edition?: string; path: string; missing?: boolean;
  progress?: { done: number; total: number; percent: number }; last_opened?: string; has_cover?: boolean;
}
export interface Issue {
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"; code: string; message: string; page?: number | null;
  block_id?: string | null; category: string;
}
export interface BlockInfo {
  block_id: string; type: string; section_id: string; heading_path: string[]; source_pages: number[];
  bboxes: { page: number; bbox: number[] }[]; markdown: string; md_line_start: number; confidence: number;
}
export interface ChapterContent {
  chapter: Chapter; markdown: string; blocks: BlockInfo[]; issues: Issue[]; status: string; report: string;
  markdown_file: string; page_sizes: Record<string, [number, number]>;
}
export interface SearchResult {
  chapter: string; chapter_no: number | null; chapter_title: string; section_id: string; heading_path: string;
  snippet: string; type: string; pages: number[]; page: number; block_id: string; md_file: string; line: number;
}
export interface Job {
  id: string; project: string; project_path: string; chapters: string[]; status: string; error?: string;
  progress: { chapter?: string; stage?: string; page?: number; done?: number; total?: number; chapter_index?: number; chapter_total?: number };
  results: Record<string, { status: string; error?: string }>; log: string[];
}
export interface Settings {
  ai_enabled: boolean; provider: "openai" | "gemini"; openai_api_key: string; gemini_api_key: string;
  openai_model: string; gemini_model: string; ocr_enabled: boolean; ocr_lang: string; output_dir: string;
  theme: "system" | "light" | "dark"; markdown_mode: "standard" | "obsidian";
  openai_api_key_set?: boolean; gemini_api_key_set?: boolean;
}
export type View = "library" | "new" | "overview" | "convert" | "search" | "review" | "export" | "settings";
export interface Nav { view: View; chapter?: string; page?: number; block?: string; query?: string; focus?: "pdf" | "md" }
