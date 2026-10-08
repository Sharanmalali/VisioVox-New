'use client';

/**
 * Which language the speakers use, for captions.
 *
 * "Detect automatically" is the default and sends no language. A choice is
 * honoured exactly by the recognizer: automatic detection misreads accented
 * speech (docs/29 §7), and the person uploading usually knows.
 *
 * The list is the server's (`/v1/limits` → `caption_languages`), so this never
 * offers a language the recognizer cannot transcribe. Indian languages come
 * first and carry their own-script names.
 */

import { Select } from '@/components/ui';
import type { LanguageOption } from '@visiovox/ts-client';

/** Scheduled languages of India the recognizer does not cover; named so nobody hunts for them. */
const NOT_COVERED = 'Odia, Maithili, Konkani, Kashmiri, Manipuri, Bodo, Dogri and Santali';

function labelOf(lang: LanguageOption): string {
  return lang.native ? `${lang.name} — ${lang.native}` : lang.name;
}

export interface LanguageSelectProps {
  languages: readonly LanguageOption[] | undefined;
  value: string | null;
  onChange: (code: string | null) => void;
  disabled?: boolean | undefined;
}

export function LanguageSelect({ languages, value, onChange, disabled }: LanguageSelectProps) {
  const list = languages ?? [];
  const english = list.filter((l) => l.code === 'en');
  const india = list.filter((l) => l.group === 'india');
  const world = list.filter((l) => l.group === 'world' && l.code !== 'en');

  return (
    <div className="flex flex-col gap-2">
      <Select
        label="Spoken language"
        value={value ?? ''}
        disabled={disabled === true || list.length === 0}
        hint={`For captions only — separation works in any language. Choose it when accents may confuse detection. Not yet available: ${NOT_COVERED}.`}
        onChange={(e) => {
          onChange(e.target.value === '' ? null : e.target.value);
        }}
      >
        <option value="">Detect automatically</option>
        {english.map((l) => (
          <option key={l.code} value={l.code}>
            {labelOf(l)}
          </option>
        ))}
        {india.length > 0 && (
          <optgroup label="Indian languages">
            {india.map((l) => (
              <option key={l.code} value={l.code}>
                {labelOf(l)}
              </option>
            ))}
          </optgroup>
        )}
        {world.length > 0 && (
          <optgroup label="Other languages">
            {world.map((l) => (
              <option key={l.code} value={l.code}>
                {labelOf(l)}
              </option>
            ))}
          </optgroup>
        )}
      </Select>
    </div>
  );
}
