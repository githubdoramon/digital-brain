const localPathPattern = /(?:file|content):\/\/[^\s"']+/gi;
const absolutePathPattern = /\/(?:data|storage|private|Users)\/[^\s"']+/gi;
const urlQueryPattern = /(https?:\/\/[^\s?"']+)\?[^\s"']+/gi;

export function redactDiagnosticText(value: unknown): string {
  const text = value instanceof Error ? value.message : String(value ?? 'Unknown error');
  return text
    .replace(localPathPattern, '[redacted-local-path]')
    .replace(absolutePathPattern, '[redacted-local-path]')
    .replace(urlQueryPattern, '$1?[redacted]')
    .slice(0, 2_000);
}
