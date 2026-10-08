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

import { useId } from 'react';

import { cx } from '@/components/ui';
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
  const id = useId();
  const noteId = `${id}-note`;
  const list = languages ?? [];
  const english = list.filter((l) => l.code === 'en');
  const india = list.filter((l) => l.group === 'india');
  const world = list.filter((l) => l.group === 'world' && l.code !== 'en');

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="etch">
        Spoken language
      </label>
      <select
        id={id}
        value={value ?? ''}
        disabled={disabled === true || list.length === 0}
        aria-describedby={noteId}
        onChange={(e) => {
          onChange(e.target.value === '' ? null : e.target.value);
        }}
        className={cx(
          'w-full border-4 border-edge bg-void px-4 py-3 text-sm text-chalk',
          'shadow-[var(--depth-inset)] focus:border-acid disabled:opacity-60',
        )}
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
      </select>
      <span id={noteId} className="font-mono text-[11px] text-dust">
        Used for captions only; separation works in any language. Choose it when speakers have an
        accent that detection may misread. Captions cannot yet be made in {NOT_COVERED}: speech in
        those comes out in the closest language the recognizer knows.
      </span>
    </div>
  );
}
