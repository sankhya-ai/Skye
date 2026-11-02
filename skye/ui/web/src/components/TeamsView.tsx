// Teams: saved lead + member rosters the owner creates once and reuses per job.
// A saved team grants nothing by itself - on a Team task the lead plans and the engine
// rejects any step it proposes for a role outside the roster.
import { Crown, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { Select } from "./ui/Select";
import {
  deleteTeam,
  getAgentDefinitions,
  getProjects,
  getRoles,
  getTeams,
  saveTeam,
  type AgentDefinition,
  type Project,
  type RoleRow,
  type TeamRow,
} from "../api";
import { humanError } from "../lib";

function MemberPicker({
  roles,
  selected,
  lead,
  onToggle,
}: {
  roles: RoleRow[];
  selected: string[];
  lead: string;
  onToggle: (role: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {roles.map((role) => {
        const active = selected.includes(role.name);
        const isLead = role.name === lead;
        return (
          <button
            key={role.name}
            type="button"
            disabled={isLead}
            title={isLead ? "The lead plans; it is not its own member" : role.description || role.name}
            onClick={() => onToggle(role.name)}
            className={`rounded-full border px-2.5 py-1 font-mono text-xs transition-colors ${
              active
                ? "border-primary bg-primary text-primary-foreground"
                : isLead
                  ? "border-border text-muted-foreground/50"
                  : "border-border text-foreground/80 hover:text-foreground"
            }`}
          >
            {role.name}
          </button>
        );
      })}
    </div>
  );
}

export function TeamsView() {
  const [teams, setTeams] = useState<TeamRow[]>([]);
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [defs, setDefs] = useState<Map<string, AgentDefinition>>(new Map());
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [lead, setLead] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [teamProjects, setTeamProjects] = useState<string[]>([]);
  const [handoff, setHandoff] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [teamRes, roleRes, agentRes, projectRes] = await Promise.all([
        getTeams(),
        getRoles(),
        getAgentDefinitions(),
        getProjects(),
      ]);
      setTeams(teamRes.teams);
      setRoles(roleRes.roles);
      setDefs(new Map(agentRes.agents.map((d) => [d.name, d])));
      setProjects(projectRes.projects);
      setError(null);
    } catch (e) {
      setError(humanError(e, "teams"));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The lead runs the planning session, which is a nonwriting context step (the same
  // invariant the composer enforces): write-capable roles stay VISIBLE as lead options but
  // disabled with the reason, never silently missing. Any read-only role can lead - pm is
  // not special.
  const active = roles.filter((r) => defs.get(r.name)?.status !== "proposed");
  const leadOptions = active.map((r) => ({
    value: r.name,
    label: r.name,
    hint: r.catalogue.writable
      ? "Writes code - the lead only plans, so it must be read-only"
      : r.description || undefined,
    disabled: r.catalogue.writable,
  }));
  const leadChoices = active.filter((r) => !r.catalogue.writable);
  const memberChoices = active;

  useEffect(() => {
    // pm is the sensible default lead (it is the planner people know); any read-only
    // role qualifies when pm is not there.
    if (!lead && leadChoices.length > 0) {
      setLead(leadChoices.some((r) => r.name === "pm") ? "pm" : leadChoices[0].name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roles, defs]);

  const reset = () => {
    setEditingId(null);
    setName("");
    setMembers([]);
    setTeamProjects([]);
    setHandoff("");
  };

  const edit = (team: TeamRow) => {
    setEditingId(team.id);
    setName(team.name);
    setLead(team.lead);
    setMembers(team.members);
    setTeamProjects(team.projects ?? []);
    setHandoff(team.handoff ?? "");
  };

  const save = async () => {
    setError(null);
    setSaving(true);
    try {
      await saveTeam({
        ...(editingId ? { id: editingId } : {}),
        name: name.trim(),
        lead,
        members,
        handoff: handoff.trim(),
        projects: teamProjects,
      });
      reset();
      await refresh();
    } catch (e) {
      setError(humanError(e, "team"));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (team: TeamRow) => {
    setError(null);
    try {
      await deleteTeam(team.id);
      if (editingId === team.id) reset();
      await refresh();
    } catch (e) {
      setError(humanError(e, "team"));
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Teams</h2>
        <p className="text-sm text-muted-foreground">
          A saved team is a lead plus the members it may delegate to. Pick one when you start a
          Team task: the lead plans the work, and Skye enforces the roster - the lead can only
          delegate to its members, and every member session still needs the usual approval to run.
        </p>
      </div>
      {error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {teams.length === 0 ? (
        <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
          No saved teams yet. Create one below - for example a pm leading a designer and a
          frontend-dev for your website work.
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        {teams.map((team) => (
          <div key={team.id} className="shadow-card rounded-xl border border-border bg-card p-4">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => edit(team)}
                className="font-medium text-foreground hover:underline"
                title="Edit this team"
              >
                {team.name}
              </button>
              <Badge variant="secondary" title={team.lead_description || team.lead}>
                <Crown className="size-3" /> {team.lead}
              </Badge>
              <button
                type="button"
                onClick={() => void remove(team)}
                className="ml-auto text-muted-foreground hover:text-destructive"
                aria-label={`Delete team ${team.name}`}
                title="Delete this team"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(team.member_details ?? team.members.map((m) => ({ name: m, description: "" }))).map(
                (member) => (
                  <span
                    key={member.name}
                    title={member.description || member.name}
                    className="rounded-full border border-border px-2 py-0.5 font-mono text-xs text-foreground/80"
                  >
                    {member.name}
                  </span>
                ),
              )}
            </div>
            {(team.project_names ?? []).length > 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                Assigned to {(team.project_names ?? []).join(", ")}
              </p>
            ) : (
              <p className="mt-2 text-xs text-muted-foreground/70">Works on any project</p>
            )}
            {team.handoff ? (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-muted-foreground/80 hover:text-foreground">
                  Handoff notes
                </summary>
                <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{team.handoff}</p>
              </details>
            ) : null}
          </div>
        ))}
      </div>
      <div className="shadow-card rounded-xl border border-border bg-card p-4">
        <h3 className="mb-3 flex items-center gap-1 text-sm font-medium">
          <Plus className="size-4" /> {editingId ? "Edit team" : "New team"}
        </h3>
        <div className="grid gap-2 sm:grid-cols-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="team name (e.g. website)"
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none"
          />
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="shrink-0">Lead</span>
            <Select
              value={lead}
              ariaLabel="Team lead"
              className="flex-1"
              options={leadOptions}
              onChange={setLead}
            />
          </label>
        </div>
        <p className="mb-1 mt-3 text-xs text-muted-foreground">
          Members the lead may delegate to (picked {members.length})
        </p>
        <MemberPicker
          roles={memberChoices}
          selected={members}
          lead={lead}
          onToggle={(role) =>
            setMembers((current) =>
              current.includes(role) ? current.filter((r) => r !== role) : [...current, role],
            )
          }
        />
        {projects.length > 0 ? (
          <>
            <p className="mb-1 mt-3 text-xs text-muted-foreground">
              Assigned projects (none picked: works on any project)
            </p>
            <div className="flex flex-wrap gap-1.5">
              {projects.map((project) => {
                const active = teamProjects.includes(project.id);
                return (
                  <button
                    key={project.id}
                    type="button"
                    title={project.repo}
                    onClick={() =>
                      setTeamProjects((current) =>
                        active ? current.filter((p) => p !== project.id) : [...current, project.id],
                      )
                    }
                    className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                      active
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border text-foreground/80 hover:text-foreground"
                    }`}
                  >
                    {project.name}
                  </button>
                );
              })}
            </div>
          </>
        ) : null}
        <textarea
          value={handoff}
          onChange={(e) => setHandoff(e.target.value)}
          placeholder="Handoff notes for this team (optional) - e.g. the designer posts assets on the team line before the frontend-dev builds"
          rows={2}
          className="mt-3 w-full resize-none rounded-md border border-border bg-background px-3 py-1.5 text-xs outline-none"
        />
        <div className="mt-3 flex items-center gap-2">
          <Button size="sm" disabled={!name.trim() || !lead || members.length === 0} loading={saving}
                  onClick={() => void save()}>
            {editingId ? "Save team" : "Create team"}
          </Button>
          {editingId ? (
            <Button size="sm" variant="outline" onClick={reset}>
              Cancel edit
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
