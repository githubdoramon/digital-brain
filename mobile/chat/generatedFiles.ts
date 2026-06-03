export type GeneratedFile = {
  kind: 'generated_pdf';
  artifact_id: string;
  title: string;
  filename?: string | null;
  file_mime?: string | null;
  file_size?: number | null;
  download_url?: string | null;
  web_download_url?: string | null;
  mobile_download_url?: string | null;
};

export function generatedFileLabel(file: GeneratedFile): string {
  return file.title?.trim() || file.filename?.trim() || 'Generated PDF';
}
