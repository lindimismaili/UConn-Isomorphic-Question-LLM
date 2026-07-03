import { useMemo, useState } from 'react';
import { Card, CardBody, CardHeader, KeyValueTable, TextInput } from '../../components/ui/index.js';
import { Grid, SpecimenTile, Stack } from '../Specimen.jsx';
import { MANIFEST } from '../manifest.jsx';

/** Fixed section order (matches the manifest's `group` values). */
const GROUP_ORDER = [
  'Buttons',
  'Form controls',
  'Toggles',
  'Cards',
  'Status & feedback',
  'Tabs',
  'Tables',
  'Shell',
  'Chat',
  'Process rail',
  'Overlays',
];

/**
 * Components: every `ui/` primitive, driven by the manifest
 * (`src/design-system/manifest.jsx`) instead of hand-authored specimens. One
 * Card per group; each tile shows a live specimen, the prop contract, and a
 * copyable usage snippet. Filterable by name/summary/group.
 */
export default function Components() {
  const [query, setQuery] = useState('');
  const normalized = query.trim().toLowerCase();

  const filtered = useMemo(() => {
    if (!normalized) return MANIFEST;
    return MANIFEST.filter((entry) =>
      [entry.name, entry.summary, entry.group].some((field) => field.toLowerCase().includes(normalized)),
    );
  }, [normalized]);

  const byGroup = useMemo(() => {
    const map = new Map();
    for (const entry of filtered) {
      if (!map.has(entry.group)) map.set(entry.group, []);
      map.get(entry.group).push(entry);
    }
    return map;
  }, [filtered]);

  return (
    <Stack gap="var(--section-gap)">
      <h2 id="components" style={{ scrollMarginTop: '80px' }}>
        Components
      </h2>
      <p className="subtitle">
        Every <code>ui/</code> primitive with its prop contract and a live specimen, generated from the
        manifest (<code>src/design-system/manifest.jsx</code>). Compose features only from these.
      </p>

      <TextInput
        placeholder="Search components by name, summary, or group…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        aria-label="Search components"
      />

      {GROUP_ORDER.map((group) => {
        const entries = byGroup.get(group);
        if (!entries || entries.length === 0) return null;
        return (
          <Card key={group}>
            <CardHeader title={group} />
            <CardBody>
              <Grid min="280px">
                {entries.map((entry) => {
                  const Specimen = entry.Specimen;
                  return (
                    <SpecimenTile key={entry.name}>
                      <div>
                        <Specimen />
                      </div>
                      <h3 style={{ margin: 0 }}>{entry.name}</h3>
                      <p className="subtitle" style={{ margin: 0 }}>
                        {entry.summary}
                      </p>
                      <KeyValueTable
                        rows={entry.props.map((prop) => ({
                          key: <code>{prop.name}</code>,
                          value: prop.type,
                        }))}
                      />
                      {entry.usage ? (
                        <pre style={{ margin: 0, overflowX: 'auto' }}>
                          <code>{entry.usage}</code>
                        </pre>
                      ) : null}
                    </SpecimenTile>
                  );
                })}
              </Grid>
            </CardBody>
          </Card>
        );
      })}
    </Stack>
  );
}
