export type ContactChangeGraph = {
  people: { ref: string; display_name: string; birthday: string | null }[];
  relationships: {
    from_ref: string;
    to_ref: string;
    relationship_type: string;
    reciprocal_type: string;
  }[];
};

type ProposalActionInput = {
  kind: string;
  payload?: {
    contact_changes?: ContactChangeGraph;
    requested_status?: string;
    target_status?: string;
  };
};

export function proposalAction(
  proposal: ProposalActionInput,
  resolutions: Record<string, string> = {},
): { label: string; supported: boolean; title?: string } {
  const supported = (label: string) => ({ label, supported: true });
  switch (proposal.kind) {
    case 'profession':
      return supported('Add profession');
    case 'relationship':
      return supported('Add relationship');
    case 'task':
      return supported('Create task');
    case 'project':
      return supported('Create project');
    case 'task_change':
      return supported(
        proposal.payload?.requested_status === 'cancelled'
          ? 'Cancel task'
          : proposal.payload?.target_status === 'completed'
            ? 'Reopen task'
            : 'Update task',
      );
    case 'contact_change': {
      const graph = proposal.payload?.contact_changes;
      if (!graph?.people.length) break;
      const contactAction = (label: string) => ({
        ...supported(label),
        title: graph.people.map((person) => person.display_name).join(' · '),
      });
      const creates = graph.people.filter(
        (person) =>
          person.ref.startsWith('new:') &&
          (!resolutions[person.ref] || resolutions[person.ref] === 'create'),
      ).length;
      const birthdays = graph.people.filter((person) => person.birthday).length;
      if (graph.relationships.length) {
        return contactAction(creates || birthdays ? 'Apply contact changes' : 'Add relationship');
      }
      if (creates) {
        const contactLabel = creates === 1 ? 'Create contact' : 'Create contacts';
        return contactAction(
          birthdays
            ? `${contactLabel} and add ${birthdays === 1 ? 'birthday' : 'birthdays'}`
            : contactLabel,
        );
      }
      if (birthdays) return contactAction(birthdays === 1 ? 'Add birthday' : 'Add birthdays');
      return contactAction('Link contact');
    }
  }
  // Never present an unfamiliar or incomplete action as a profession change.
  return { label: 'Review suggestion', supported: false };
}
