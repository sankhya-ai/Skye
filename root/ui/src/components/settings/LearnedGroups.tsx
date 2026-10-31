// What root picked up from working with you, kept simple: suggestions waiting
// for a yes or no, the skills it keeps, and the commands it now runs without
// asking. Each is a plain list with one action per row.

import { useCallback, useEffect, useState } from "react";

import { api } from "../../lib/api.ts";
import type { LearnedSkill, LearningCandidate, TrustEntry } from "../../lib/types.ts";
import { Group, Row } from "./controls.tsx";

export function LearnedGroups() {
  const [candidates, setCandidates] = useState<LearningCandidate[]>([]);
  const [skills, setSkills] = useState<LearnedSkill[]>([]);
  const refresh = useCallback(() => {
    api.learning().then(
      (d) => {
        setCandidates(d.candidates.filter((c) => c.status === "candidate"));
        setSkills(d.skills);
      },
      () => undefined,
    );
  }, []);
  useEffect(refresh, [refresh]);

  return (
    <>
      {candidates.length ? (
        <Group title="Suggestions">
          {candidates.map((c) => (
            <Row key={c.id} label={c.title} help={c.review_summary || c.body.lesson || c.body.content || ""}>
              <div className="settingsInline">
                <button className="pill" onClick={() => api.reject(c.id).then(refresh)}>
                  Discard
                </button>
                <button className="pill ink" onClick={() => api.promote(c.id).then(refresh)}>
                  Keep
                </button>
              </div>
            </Row>
          ))}
        </Group>
      ) : null}
      {skills.length ? (
        <Group title="Skills root learned">
          {skills.map((skill) => (
            <Row
              key={skill.name}
              label={skill.name}
              help={
                skill.outcome?.underperforming
                  ? `${skill.description} — going worse than before it; root will roll it back.`
                  : skill.outcome?.uses
                    ? `${skill.description} · ${skill.outcome.clean} of ${skill.outcome.uses} turns clean`
                    : skill.description
              }
            >
              <button className="pill" onClick={() => api.forgetSkill(skill.name).then(refresh)}>
                Remove
              </button>
            </Row>
          ))}
        </Group>
      ) : null}
    </>
  );
}

export function TrustedCommandsGroup() {
  const [entries, setEntries] = useState<TrustEntry[] | null>(null);
  const refresh = useCallback(() => {
    api.trust().then((d) => setEntries(d.entries.filter((e) => e.promoted)), () => setEntries([]));
  }, []);
  useEffect(refresh, [refresh]);
  if (!entries) return null;
  return (
    <Group title="Runs without asking">
      {entries.length ? (
        entries.map((entry) => (
          <Row key={entry.key} label={<span className="mono">{entry.key.replace(/^run command:/, "")}</span>} help={`You approved it ${entry.approvals} times.`}>
            <button className="pill" onClick={() => api.revokeTrust(entry.key).then(refresh)}>
              Remove
            </button>
          </Row>
        ))
      ) : (
        <Row label="Nothing yet" help="When you approve the same kind of check a few times (say, your test command), root stops asking for it. It shows up here." />
      )}
    </Group>
  );
}
