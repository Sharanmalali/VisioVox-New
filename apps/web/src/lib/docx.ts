/**
 * A Word document, written by hand.
 *
 * A .docx is a ZIP of a few XML parts. The libraries that write one weigh more
 * than the rest of the project page together; what a transcript needs - a
 * title, a few facts, a table, and paragraphs with a coloured speaker name -
 * is a small, fixed subset of WordprocessingML. The ZIP is written uncompressed
 * (method 0, "stored"), which every reader accepts and which needs only CRC-32.
 *
 * Runs in the browser at click time; nothing here is in the first load.
 */

// --------------------------------------------------------------------- zip --

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = (CRC_TABLE[(crc ^ byte) & 0xff] ?? 0) ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

interface ZipEntry {
  readonly name: string;
  readonly data: Uint8Array;
}

/** A stored (uncompressed) ZIP archive. */
export function zip(entries: readonly ZipEntry[]): Uint8Array<ArrayBuffer> {
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  // 1980-01-01 00:00 - a fixed stamp keeps the output byte-for-byte stable.
  const DOS_TIME = 0;
  const DOS_DATE = (0 << 9) | (1 << 5) | 1;

  for (const entry of entries) {
    const name = encoder.encode(entry.name);
    const crc = crc32(entry.data);
    const size = entry.data.length;

    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true); // version needed
    local.setUint16(6, 0x0800, true); // UTF-8 names
    local.setUint16(8, 0, true); // stored
    local.setUint16(10, DOS_TIME, true);
    local.setUint16(12, DOS_DATE, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, size, true);
    local.setUint32(22, size, true);
    local.setUint16(26, name.length, true);
    local.setUint16(28, 0, true);
    chunks.push(new Uint8Array(local.buffer), name, entry.data);

    const dir = new DataView(new ArrayBuffer(46));
    dir.setUint32(0, 0x02014b50, true);
    dir.setUint16(4, 20, true); // version made by
    dir.setUint16(6, 20, true);
    dir.setUint16(8, 0x0800, true);
    dir.setUint16(10, 0, true);
    dir.setUint16(12, DOS_TIME, true);
    dir.setUint16(14, DOS_DATE, true);
    dir.setUint32(16, crc, true);
    dir.setUint32(20, size, true);
    dir.setUint32(24, size, true);
    dir.setUint16(28, name.length, true);
    dir.setUint32(42, offset, true);
    central.push(new Uint8Array(dir.buffer), name);

    offset += 30 + name.length + size;
  }

  const centralSize = central.reduce((n, c) => n + c.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, entries.length, true);
  end.setUint16(10, entries.length, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, offset, true);

  const parts = [...chunks, ...central, new Uint8Array(end.buffer)];
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

// ------------------------------------------------------------------- docx --

export type DocBlock =
  | { readonly kind: 'title'; readonly text: string }
  | { readonly kind: 'meta'; readonly text: string }
  | { readonly kind: 'heading'; readonly text: string }
  | { readonly kind: 'paragraph'; readonly text: string }
  | {
      readonly kind: 'table';
      readonly header: readonly string[];
      readonly rows: readonly (readonly string[])[];
    }
  | {
      readonly kind: 'turn';
      readonly speaker: string;
      /** Hex without '#', for the speaker's name. */
      readonly color: string;
      readonly time: string;
      readonly text: string;
    };

function xml(text: string): string {
  return (
    text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      // XML 1.0 forbids most control characters outright.
      // eslint-disable-next-line no-control-regex
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
  );
}

function run(text: string, props = ''): string {
  return `<w:r>${props ? `<w:rPr>${props}</w:rPr>` : ''}<w:t xml:space="preserve">${xml(text)}</w:t></w:r>`;
}

function para(runs: string, props = ''): string {
  return `<w:p>${props ? `<w:pPr>${props}</w:pPr>` : ''}${runs}</w:p>`;
}

function block(b: DocBlock): string {
  switch (b.kind) {
    case 'title':
      return para(run(b.text, '<w:b/><w:sz w:val="40"/>'), '<w:spacing w:after="80"/>');
    case 'meta':
      return para(run(b.text, '<w:color w:val="636A78"/><w:sz w:val="20"/>'));
    case 'heading':
      return para(
        run(b.text, '<w:b/><w:sz w:val="28"/>'),
        '<w:spacing w:before="360" w:after="120"/><w:keepNext/>',
      );
    case 'paragraph':
      return para(run(b.text));
    case 'turn':
      return (
        para(
          run(b.speaker, `<w:b/><w:color w:val="${b.color}"/>`) +
            run(`  ${b.time}`, '<w:color w:val="8A909C"/><w:sz w:val="18"/>'),
          '<w:spacing w:before="200" w:after="40"/><w:keepNext/>',
        ) + para(run(b.text))
      );
    case 'table': {
      const cell = (text: string, header: boolean): string =>
        `<w:tc><w:tcPr><w:tcMar><w:top w:w="60" w:type="dxa"/><w:bottom w:w="60" w:type="dxa"/></w:tcMar></w:tcPr>${para(
          run(text, header ? '<w:b/>' : ''),
        )}</w:tc>`;
      const row = (cells: readonly string[], header: boolean): string =>
        `<w:tr>${cells.map((c) => cell(c, header)).join('')}</w:tr>`;
      const border = ['top', 'bottom', 'insideH']
        .map((edge) => `<w:${edge} w:val="single" w:sz="4" w:color="D9DCE3"/>`)
        .join('');
      return (
        `<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>${border}</w:tblBorders></w:tblPr>` +
        row(b.header, true) +
        b.rows.map((r) => row(r, false)).join('') +
        '</w:tbl>' +
        para('')
      );
    }
  }
}

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>`;

const ROOT_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>`;

const DOC_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`;

// Nirmala UI covers every Indian script Windows ships; Calibri the rest.
const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Nirmala UI"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="0B0D12"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults></w:styles>`;

/** The bytes of a .docx containing the given blocks. */
export function buildDocx(blocks: readonly DocBlock[]): Uint8Array<ArrayBuffer> {
  const body = blocks.map(block).join('');
  const document = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1200" w:right="1200" w:bottom="1200" w:left="1200" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>`;
  const encoder = new TextEncoder();
  return zip([
    { name: '[Content_Types].xml', data: encoder.encode(CONTENT_TYPES) },
    { name: '_rels/.rels', data: encoder.encode(ROOT_RELS) },
    { name: 'word/document.xml', data: encoder.encode(document) },
    { name: 'word/_rels/document.xml.rels', data: encoder.encode(DOC_RELS) },
    { name: 'word/styles.xml', data: encoder.encode(STYLES) },
  ]);
}

export const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
