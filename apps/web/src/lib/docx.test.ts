import { describe, expect, it } from 'vitest';

import { buildDocx, crc32, zip } from './docx';

/** Read a stored ZIP back through its central directory. */
function unzip(bytes: Uint8Array): Map<string, string> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const end = bytes.length - 22;
  expect(view.getUint32(end, true)).toBe(0x06054b50);
  const count = view.getUint16(end + 10, true);
  let at = view.getUint32(end + 16, true);
  const decoder = new TextDecoder();
  const files = new Map<string, string>();
  for (let i = 0; i < count; i++) {
    expect(view.getUint32(at, true)).toBe(0x02014b50);
    const crc = view.getUint32(at + 16, true);
    const size = view.getUint32(at + 24, true);
    const nameLength = view.getUint16(at + 28, true);
    const local = view.getUint32(at + 42, true);
    const name = decoder.decode(bytes.subarray(at + 46, at + 46 + nameLength));
    expect(view.getUint32(local, true)).toBe(0x04034b50);
    const start = local + 30 + view.getUint16(local + 26, true);
    const data = bytes.subarray(start, start + size);
    expect(crc32(data)).toBe(crc);
    files.set(name, decoder.decode(data));
    at += 46 + nameLength;
  }
  return files;
}

describe('crc32', () => {
  it('matches the reference value', () => {
    expect(crc32(new TextEncoder().encode('123456789'))).toBe(0xcbf43926);
  });
});

describe('zip', () => {
  it('round-trips names and contents, including non-ASCII', () => {
    const encoder = new TextEncoder();
    const files = unzip(
      zip([
        { name: 'a.txt', data: encoder.encode('hello') },
        { name: 'ಕನ್ನಡ.txt', data: encoder.encode('ನಮಸ್ಕಾರ') },
      ]),
    );
    expect(files.get('a.txt')).toBe('hello');
    expect(files.get('ಕನ್ನಡ.txt')).toBe('ನಮಸ್ಕಾರ');
  });
});

describe('buildDocx', () => {
  const files = unzip(
    buildDocx([
      { kind: 'title', text: 'Panel <review> & notes' },
      { kind: 'table', header: ['Speaker', 'Talk time'], rows: [['Priya', '62%']] },
      { kind: 'turn', speaker: 'Priya', color: '1F6FC0', time: '00:05', text: 'नमस्ते' },
    ]),
  );

  it('has every part Word requires', () => {
    expect([...files.keys()].sort()).toEqual(
      [
        '[Content_Types].xml',
        '_rels/.rels',
        'word/_rels/document.xml.rels',
        'word/document.xml',
        'word/styles.xml',
      ].sort(),
    );
  });

  it('escapes text and keeps Indic script', () => {
    const doc = files.get('word/document.xml') ?? '';
    expect(doc).toContain('Panel &lt;review&gt; &amp; notes');
    expect(doc).toContain('नमस्ते');
    expect(doc).toContain('w:color w:val="1F6FC0"');
    expect(doc).toContain('<w:tbl>');
  });
});
