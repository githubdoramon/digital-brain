import React from 'react';
import { Linking, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';

import { theme } from '@/theme';

// ---------------------------------------------------------------------------
// Patterns
// ---------------------------------------------------------------------------

const INLINE_MARKDOWN_PATTERN =
  /(\[[^\]]+\]\((?:https?:\/\/|mailto:|www\.)[^)\s]+\)|(?:https?:\/\/|mailto:|www\.)\S+|\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
const MARKDOWN_LINK_PATTERN = /^\[([^\]]+)\]\(([^)\s]+)\)$/;
const URL_TOKEN_PATTERN = /^(?:https?:\/\/|mailto:|www\.)\S+$/;
const TRAILING_URL_PUNCTUATION_PATTERN = /[),.!?;:]+$/;
const BULLET_LINE_PATTERN = /^[-*]\s+/;
const NUMBERED_LINE_PATTERN = /^(\d+)\.\s+(.*)$/;
const BLOCKQUOTE_LINE_PATTERN = /^>\s+/;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeLinkUrl(url: string) {
  const trimmed = url.trim();
  if (trimmed.startsWith('www.')) {
    return `https://${trimmed}`;
  }
  return trimmed;
}

async function openMarkdownLink(rawUrl: string) {
  const url = normalizeLinkUrl(rawUrl);
  try {
    await Linking.openURL(url);
  } catch (error) {
    console.warn('Failed to open markdown link', error);
  }
}

function splitTrailingUrlPunctuation(token: string) {
  const trailing = token.match(TRAILING_URL_PUNCTUATION_PATTERN)?.[0] ?? '';
  if (!trailing) {
    return { url: token, trailingText: '' };
  }
  return {
    url: token.slice(0, -trailing.length),
    trailingText: trailing,
  };
}

// ---------------------------------------------------------------------------
// Inline markdown
// ---------------------------------------------------------------------------

function renderInlineMarkdown(text: string, keyPrefix: string) {
  const parts = text.split(INLINE_MARKDOWN_PATTERN).filter(Boolean);
  return parts.map((part, index) => {
    const markdownLinkMatch = part.match(MARKDOWN_LINK_PATTERN);
    if (markdownLinkMatch) {
      const [, label, rawUrl] = markdownLinkMatch;
      return (
        <Text
          key={`${keyPrefix}-link-${index}`}
          style={styles.markdownLink}
          accessibilityRole="link"
          selectable={false}
          onPress={() => {
            void openMarkdownLink(rawUrl);
          }}
        >
          {label}
        </Text>
      );
    }

    if (URL_TOKEN_PATTERN.test(part)) {
      const { url, trailingText } = splitTrailingUrlPunctuation(part);
      return (
        <React.Fragment key={`${keyPrefix}-url-${index}`}>
          <Text
            style={styles.markdownLink}
            accessibilityRole="link"
            selectable={false}
            onPress={() => {
              void openMarkdownLink(url);
            }}
          >
            {url}
          </Text>
          {trailingText ? <Text selectable>{trailingText}</Text> : null}
        </React.Fragment>
      );
    }

    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <Text key={`${keyPrefix}-bold-${index}`} style={styles.markdownBold} selectable>
          {part.slice(2, -2)}
        </Text>
      );
    }
    if (part.startsWith('*') && part.endsWith('*')) {
      return (
        <Text key={`${keyPrefix}-italic-${index}`} style={styles.markdownItalic} selectable>
          {part.slice(1, -1)}
        </Text>
      );
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <Text key={`${keyPrefix}-code-${index}`} style={styles.markdownInlineCode} selectable>
          {part.slice(1, -1)}
        </Text>
      );
    }
    return (
      <Text key={`${keyPrefix}-text-${index}`} selectable>
        {part}
      </Text>
    );
  });
}

// ---------------------------------------------------------------------------
// Code blocks
// ---------------------------------------------------------------------------

function flushCodeBlock(
  blocks: React.ReactNode[],
  codeLines: string[],
  keyPrefix: string,
  codeBlockCount: number,
) {
  blocks.push(
    <View key={`${keyPrefix}-code-block-${codeBlockCount}`} style={styles.markdownCodeBlock}>
      <Text style={styles.markdownCodeText} selectable>
        {codeLines.join('\n')}
      </Text>
    </View>,
  );
}

// ---------------------------------------------------------------------------
// Tables
// ---------------------------------------------------------------------------

function isTableLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith('|') && trimmed.endsWith('|') && trimmed.length > 1;
}

function isTableSeparatorLine(line: string): boolean {
  const trimmed = line.trim();
  return isTableLine(line) && /^[|\s:-]+$/.test(trimmed);
}

function parseTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

/** Approximate character-based width for a cell string. */
function estimateCellWidth(text: string): number {
  // ~8px per character at font size 13, plus cell padding (10*2)
  return text.length * 8 + 20;
}

const TABLE_CELL_MIN_WIDTH = 72;
const TABLE_CELL_MAX_WIDTH = 220;

function renderMarkdownTable(
  tableLines: string[],
  keyPrefix: string,
  startIndex: number,
) {
  // Parse header, separator, and body rows
  const rows: string[][] = [];
  let headerRowCount = 0;

  for (let i = 0; i < tableLines.length; i++) {
    if (isTableSeparatorLine(tableLines[i])) {
      headerRowCount = i;
      continue;
    }
    rows.push(parseTableRow(tableLines[i]));
  }

  if (headerRowCount === 0 && rows.length > 1) {
    headerRowCount = 1;
  }

  // Determine uniform column count and widths
  const columnCount = Math.max(...rows.map((r) => r.length), 0);
  if (columnCount === 0) return null;

  // Compute max content width per column across all rows
  const columnWidths: number[] = new Array(columnCount).fill(TABLE_CELL_MIN_WIDTH);
  for (const row of rows) {
    for (let col = 0; col < columnCount; col++) {
      const cellText = row[col] ?? '';
      const estimated = estimateCellWidth(cellText);
      const clamped = Math.max(TABLE_CELL_MIN_WIDTH, Math.min(TABLE_CELL_MAX_WIDTH, estimated));
      if (clamped > columnWidths[col]) {
        columnWidths[col] = clamped;
      }
    }
  }

  // Normalize rows to have consistent column count
  const normalizedRows = rows.map((row) => {
    if (row.length >= columnCount) return row.slice(0, columnCount);
    return [...row, ...new Array(columnCount - row.length).fill('')];
  });

  return (
    <ScrollView
      key={`${keyPrefix}-table-${startIndex}`}
      horizontal
      showsHorizontalScrollIndicator
      style={styles.markdownTableScroll}
    >
      <View style={styles.markdownTable}>
        {normalizedRows.map((cells, rowIndex) => {
          const isHeader = rowIndex < headerRowCount;
          return (
            <View
              key={`${keyPrefix}-table-row-${startIndex}-${rowIndex}`}
              style={[
                styles.markdownTableRow,
                isHeader && styles.markdownTableHeaderRow,
                rowIndex < normalizedRows.length - 1 && styles.markdownTableRowBorder,
              ]}
            >
              {cells.map((cell, cellIndex) => {
                const isLastCell = cellIndex === columnCount - 1;
                return (
                  <View
                    key={`${keyPrefix}-table-cell-${startIndex}-${rowIndex}-${cellIndex}`}
                    style={[
                      styles.markdownTableCell,
                      { width: columnWidths[cellIndex] },
                      isLastCell && styles.markdownTableCellLast,
                    ]}
                  >
                    <Text
                      style={[
                        styles.markdownTableCellText,
                        isHeader && styles.markdownTableHeaderText,
                      ]}
                      selectable
                    >
                      {renderInlineMarkdown(
                        cell,
                        `${keyPrefix}-table-cell-${startIndex}-${rowIndex}-${cellIndex}`,
                      )}
                    </Text>
                  </View>
                );
              })}
            </View>
          );
        })}
      </View>
    </ScrollView>
  );
}

// ---------------------------------------------------------------------------
// Main renderer
// ---------------------------------------------------------------------------

export function renderAssistantMarkdown(markdown: string, keyPrefix: string) {
  const blocks: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeBlockCount = 0;
  let codeLines: string[] = [];
  let tableLines: string[] = [];
  let tableStartIndex = 0;

  function flushTable() {
    if (tableLines.length > 0) {
      const node = renderMarkdownTable(tableLines, keyPrefix, tableStartIndex);
      if (node) blocks.push(node);
      tableLines = [];
    }
  }

  markdown.split('\n').forEach((line, index) => {
    const trimmedLine = line.trim();

    if (trimmedLine.startsWith('```')) {
      flushTable();
      if (inCodeBlock) {
        flushCodeBlock(blocks, codeLines, keyPrefix, codeBlockCount);
        codeLines = [];
        inCodeBlock = false;
        codeBlockCount += 1;
      } else {
        inCodeBlock = true;
      }
      return;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }

    // Table line accumulation
    if (isTableLine(line)) {
      if (tableLines.length === 0) {
        tableStartIndex = index;
      }
      tableLines.push(line);
      return;
    }

    // Non-table line: flush any accumulated table
    flushTable();

    if (!trimmedLine) {
      blocks.push(<View key={`${keyPrefix}-space-${index}`} style={styles.markdownSpacer} />);
      return;
    }

    if (line.startsWith('### ')) {
      blocks.push(
        <Text key={`${keyPrefix}-h3-${index}`} style={styles.markdownH3} selectable>
          {renderInlineMarkdown(line.replace('### ', ''), `${keyPrefix}-h3-${index}`)}
        </Text>,
      );
      return;
    }

    if (line.startsWith('## ')) {
      blocks.push(
        <Text key={`${keyPrefix}-h2-${index}`} style={styles.markdownH2} selectable>
          {renderInlineMarkdown(line.replace('## ', ''), `${keyPrefix}-h2-${index}`)}
        </Text>,
      );
      return;
    }

    if (line.startsWith('# ')) {
      blocks.push(
        <Text key={`${keyPrefix}-h1-${index}`} style={styles.markdownH1} selectable>
          {renderInlineMarkdown(line.replace('# ', ''), `${keyPrefix}-h1-${index}`)}
        </Text>,
      );
      return;
    }

    if (BULLET_LINE_PATTERN.test(line)) {
      blocks.push(
        <View key={`${keyPrefix}-bullet-${index}`} style={styles.markdownListRow}>
          <Text style={styles.markdownListMarker} selectable>
            •
          </Text>
          <Text style={styles.markdownListText} selectable>
            {renderInlineMarkdown(
              line.replace(BULLET_LINE_PATTERN, ''),
              `${keyPrefix}-bullet-${index}`,
            )}
          </Text>
        </View>,
      );
      return;
    }

    const numberedMatch = line.match(NUMBERED_LINE_PATTERN);
    if (numberedMatch) {
      blocks.push(
        <View key={`${keyPrefix}-numbered-${index}`} style={styles.markdownListRow}>
          <Text style={styles.markdownListMarker} selectable>
            {numberedMatch[1]}.
          </Text>
          <Text style={styles.markdownListText} selectable>
            {renderInlineMarkdown(numberedMatch[2], `${keyPrefix}-numbered-${index}`)}
          </Text>
        </View>,
      );
      return;
    }

    if (BLOCKQUOTE_LINE_PATTERN.test(line)) {
      blocks.push(
        <View key={`${keyPrefix}-quote-${index}`} style={styles.markdownQuote}>
          <Text style={styles.markdownQuoteText} selectable>
            {renderInlineMarkdown(
              line.replace(BLOCKQUOTE_LINE_PATTERN, ''),
              `${keyPrefix}-quote-${index}`,
            )}
          </Text>
        </View>,
      );
      return;
    }

    blocks.push(
      <Text key={`${keyPrefix}-paragraph-${index}`} style={styles.markdownParagraph} selectable>
        {renderInlineMarkdown(line, `${keyPrefix}-paragraph-${index}`)}
      </Text>,
    );
  });

  flushTable();

  if (codeLines.length > 0) {
    flushCodeBlock(blocks, codeLines, keyPrefix, codeBlockCount);
  }

  return blocks;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  markdownLink: {
    color: theme.colors.accentDeep,
    textDecorationLine: 'underline',
  },
  markdownBold: {
    fontWeight: '700',
  },
  markdownItalic: {
    fontStyle: 'italic',
  },
  markdownInlineCode: {
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
    fontSize: 14,
    lineHeight: 22,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#F5F7FA',
    borderRadius: theme.radius.md,
    paddingHorizontal: 4,
    color: theme.colors.ink,
  },
  markdownCodeBlock: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#F5F7FA',
    borderRadius: theme.radius.md,
    paddingVertical: 10,
    paddingHorizontal: 12,
  },
  markdownCodeText: {
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
    fontSize: 13,
    lineHeight: 20,
    color: theme.colors.ink,
  },
  markdownH1: {
    fontSize: 18,
    lineHeight: 26,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  markdownH2: {
    fontSize: 16,
    lineHeight: 24,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  markdownH3: {
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  markdownParagraph: {
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.ink,
  },
  markdownListRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  markdownListMarker: {
    minWidth: 16,
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.ink,
    fontWeight: '600',
  },
  markdownListText: {
    flex: 1,
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.ink,
  },
  markdownQuote: {
    borderLeftWidth: 3,
    borderLeftColor: theme.colors.line,
    paddingLeft: 10,
    paddingVertical: 2,
  },
  markdownQuoteText: {
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.mutedInk,
  },
  markdownSpacer: {
    height: 6,
  },
  markdownTableScroll: {
    marginVertical: 4,
    flexGrow: 0,
  },
  markdownTable: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: 8,
    overflow: 'hidden',
  },
  markdownTableRow: {
    flexDirection: 'row',
  },
  markdownTableHeaderRow: {
    backgroundColor: '#F5F7FA',
  },
  markdownTableRowBorder: {
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  markdownTableCell: {
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRightWidth: 1,
    borderRightColor: theme.colors.line,
  },
  markdownTableCellLast: {
    borderRightWidth: 0,
  },
  markdownTableCellText: {
    fontSize: 13,
    lineHeight: 20,
    color: theme.colors.ink,
  },
  markdownTableHeaderText: {
    fontWeight: '700',
  },
});
