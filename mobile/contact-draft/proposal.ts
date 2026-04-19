import type { CommandResult } from '@/chat/threads';
import type {
  ContactDraftModifications,
  ContactProposalDraft,
} from '@/contact-draft/types';

function textValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : String(value ?? '').trim();
}

function stringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => textValue(item)).filter(Boolean);
  }
  const text = textValue(value);
  return text
    ? text
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
    : [];
}

function listToText(values: string[]) {
  return values.join(', ');
}

function sameStringList(left: string[], right: string[]) {
  if (left.length !== right.length) return false;
  return left.every((entry, index) => entry === right[index]);
}

export function extractContactPreviewId(commandResult: CommandResult | undefined): string | null {
  if (!commandResult || typeof commandResult !== 'object') return null;
  const previewId = textValue((commandResult as Record<string, unknown>).preview_id);
  return previewId || null;
}

export function buildContactDraft(
  commandResult: CommandResult | undefined,
  previewId: string,
): ContactProposalDraft | null {
  if (!commandResult || typeof commandResult !== 'object') return null;
  const payload = commandResult as Record<string, unknown>;
  if (textValue(payload.preview_id) !== previewId) return null;
  const proposal = payload.proposal;
  if (!proposal || typeof proposal !== 'object') return null;
  const proposalData = proposal as Record<string, unknown>;

  return {
    contacts: Array.isArray(proposalData.contacts)
      ? proposalData.contacts
          .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
          .map((item) => {
            const merged = item.merged && typeof item.merged === 'object' ? (item.merged as Record<string, unknown>) : {};
            return {
              proposalId: textValue(item.proposal_id),
              reference: textValue(item.reference),
              operation: textValue(item.operation) === 'create' ? 'create' : 'update',
              source: textValue(item.source) === 'derived' ? 'derived' : 'explicit',
              displayName: textValue(merged.display_name ?? item.display_name),
              birthday: textValue(merged.birthday),
              aliasesText: listToText(stringArray(merged.aliases)),
              emailsText: listToText(stringArray(merged.emails)),
              phonesText: listToText(stringArray(merged.phones)),
              linksText: listToText(stringArray(merged.links)),
              tagsText: listToText(stringArray(merged.tags)),
              comments: textValue(merged.comments),
            };
          })
      : [],
    relationships: [
      ...((Array.isArray(proposalData.relationships) ? proposalData.relationships : []) as Record<string, unknown>[]).map(
        (item) => ({
          proposalId: textValue(item.proposal_id),
          kind: 'explicit' as const,
          fromReference: textValue(item.from_reference),
          toReference: textValue(item.to_reference),
          fromDisplayName: textValue(item.from_display_name),
          toDisplayName: textValue(item.to_display_name),
          relationshipType: textValue(item.relationship_type),
          enabled: true,
        }),
      ),
      ...((Array.isArray(proposalData.derived_relationships)
        ? proposalData.derived_relationships
        : []) as Record<string, unknown>[]).map((item) => ({
        proposalId: textValue(item.proposal_id),
        kind: 'derived' as const,
        fromReference: textValue(item.from_reference),
        toReference: textValue(item.to_reference),
        fromDisplayName: textValue(item.from_display_name),
        toDisplayName: textValue(item.to_display_name),
        relationshipType: textValue(item.relationship_type),
        enabled: true,
      })),
    ],
    places: Array.isArray(proposalData.places)
      ? proposalData.places
          .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
          .map((item) => ({
            proposalId: textValue(item.proposal_id),
            reference: textValue(item.reference),
            name: textValue(item.name),
            address: textValue(item.address),
          }))
      : [],
    placeLinks: Array.isArray(proposalData.contact_place_links)
      ? proposalData.contact_place_links
          .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
          .map((item) => ({
            proposalId: textValue(item.proposal_id),
            contactReference: textValue(item.contact_reference),
            contactDisplayName: textValue(item.contact_display_name),
            placeReference: textValue(item.place_reference),
            placeName: textValue(item.place_name),
            role: textValue(item.role),
          }))
      : [],
  };
}

export function buildContactDraftModifications(
  baseDraft: ContactProposalDraft,
  nextDraft: ContactProposalDraft,
): ContactDraftModifications {
  const modifications: ContactDraftModifications = {};

  const contactMods = nextDraft.contacts
    .map((contact) => {
      const baseContact = baseDraft.contacts.find((item) => item.proposalId === contact.proposalId);
      if (!baseContact) return null;
      const mod: Record<string, unknown> = { proposal_id: contact.proposalId };
      if (baseContact.reference !== contact.reference) mod.reference = contact.reference;
      if (baseContact.displayName !== contact.displayName) mod.display_name = contact.displayName.trim();
      if (baseContact.birthday !== contact.birthday) mod.birth_date = contact.birthday.trim() || null;
      if (baseContact.comments !== contact.comments) mod.comments = contact.comments.trim() || null;
      if (!sameStringList(stringArray(baseContact.aliasesText), stringArray(contact.aliasesText))) mod.aliases = stringArray(contact.aliasesText);
      if (!sameStringList(stringArray(baseContact.emailsText), stringArray(contact.emailsText))) mod.emails = stringArray(contact.emailsText);
      if (!sameStringList(stringArray(baseContact.phonesText), stringArray(contact.phonesText))) mod.phones = stringArray(contact.phonesText);
      if (!sameStringList(stringArray(baseContact.linksText), stringArray(contact.linksText))) mod.links = stringArray(contact.linksText);
      if (!sameStringList(stringArray(baseContact.tagsText), stringArray(contact.tagsText))) mod.tags = stringArray(contact.tagsText);
      return Object.keys(mod).length > 1 ? mod : null;
    })
    .filter((item): item is ContactDraftModifications['contacts'][number] => Boolean(item));
  if (contactMods.length > 0) modifications.contacts = contactMods;

  const relationshipMods = nextDraft.relationships
    .map((relationship) => {
      const baseRelationship = baseDraft.relationships.find((item) => item.proposalId === relationship.proposalId);
      if (!baseRelationship) return null;
      const mod: Record<string, unknown> = { proposal_id: relationship.proposalId };
      if (baseRelationship.fromReference !== relationship.fromReference) mod.from_reference = relationship.fromReference;
      if (baseRelationship.toReference !== relationship.toReference) mod.to_reference = relationship.toReference;
      if (baseRelationship.relationshipType !== relationship.relationshipType) mod.relationship_type = relationship.relationshipType.trim();
      if (baseRelationship.enabled !== relationship.enabled) mod.enabled = relationship.enabled;
      if (baseRelationship.fromDisplayName !== relationship.fromDisplayName) mod.from_display_name = relationship.fromDisplayName.trim();
      if (baseRelationship.toDisplayName !== relationship.toDisplayName) mod.to_display_name = relationship.toDisplayName.trim();
      return Object.keys(mod).length > 1 ? mod : null;
    })
    .filter((item): item is ContactDraftModifications['relationships'][number] => Boolean(item));
  if (relationshipMods.length > 0) modifications.relationships = relationshipMods;

  const placeMods = nextDraft.places
    .map((place) => {
      const basePlace = baseDraft.places.find((item) => item.proposalId === place.proposalId);
      if (!basePlace) return null;
      const mod: Record<string, unknown> = { proposal_id: place.proposalId };
      if (basePlace.reference !== place.reference) mod.reference = place.reference;
      if (basePlace.name !== place.name) mod.name = place.name.trim();
      if (basePlace.address !== place.address) mod.address = place.address.trim();
      return Object.keys(mod).length > 1 ? mod : null;
    })
    .filter((item): item is ContactDraftModifications['places'][number] => Boolean(item));
  if (placeMods.length > 0) modifications.places = placeMods;

  const linkMods = nextDraft.placeLinks
    .map((link) => {
      const baseLink = baseDraft.placeLinks.find((item) => item.proposalId === link.proposalId);
      if (!baseLink) return null;
      const mod: Record<string, unknown> = { proposal_id: link.proposalId };
      if (baseLink.contactReference !== link.contactReference) mod.contact_reference = link.contactReference;
      if (baseLink.contactDisplayName !== link.contactDisplayName) mod.contact_display_name = link.contactDisplayName.trim();
      if (baseLink.placeReference !== link.placeReference) mod.place_reference = link.placeReference;
      if (baseLink.role !== link.role) mod.role = link.role.trim();
      if (baseLink.placeName !== link.placeName) mod.place_name = link.placeName.trim();
      return Object.keys(mod).length > 1 ? mod : null;
    })
    .filter((item): item is ContactDraftModifications['contact_place_links'][number] => Boolean(item));
  if (linkMods.length > 0) modifications.contact_place_links = linkMods;

  return modifications;
}

export function applyContactDraftModifications(
  baseDraft: ContactProposalDraft,
  modifications: ContactDraftModifications | undefined,
): ContactProposalDraft {
  if (!modifications) return baseDraft;
  const contactMods = new Map((modifications.contacts || []).map((item) => [item.proposal_id, item]));
  const relationshipMods = new Map((modifications.relationships || []).map((item) => [item.proposal_id, item]));
  const placeMods = new Map((modifications.places || []).map((item) => [item.proposal_id, item]));
  const linkMods = new Map((modifications.contact_place_links || []).map((item) => [item.proposal_id, item]));

  const contacts = baseDraft.contacts.map((contact) => {
      const mod = contactMods.get(contact.proposalId);
      return mod
        ? {
            ...contact,
            reference: textValue(mod.reference) || contact.reference,
            displayName: textValue(mod.display_name) || contact.displayName,
            birthday: textValue(mod.birth_date) || '',
            aliasesText: mod.aliases ? listToText(mod.aliases) : contact.aliasesText,
            emailsText: mod.emails ? listToText(mod.emails) : contact.emailsText,
            phonesText: mod.phones ? listToText(mod.phones) : contact.phonesText,
            linksText: mod.links ? listToText(mod.links) : contact.linksText,
            tagsText: mod.tags ? listToText(mod.tags) : contact.tagsText,
            comments: mod.comments === undefined ? contact.comments : textValue(mod.comments),
          }
        : contact;
    });
  const contactNameByReference = new Map(contacts.map((contact) => [contact.reference, contact.displayName]));
  const places = baseDraft.places.map((place) => {
      const mod = placeMods.get(place.proposalId);
      return mod
        ? {
            ...place,
            reference: textValue(mod.reference) || place.reference,
            name: textValue(mod.name) || place.name,
            address: textValue(mod.address) || place.address,
          }
        : place;
    });
  const placeNameByReference = new Map(places.map((place) => [place.reference, place.name]));

  return {
    contacts,
    relationships: baseDraft.relationships.map((relationship) => {
      const mod = relationshipMods.get(relationship.proposalId);
      return mod
        ? {
            ...relationship,
            fromReference: textValue(mod.from_reference) || relationship.fromReference,
            toReference: textValue(mod.to_reference) || relationship.toReference,
            relationshipType: textValue(mod.relationship_type) || relationship.relationshipType,
            enabled: mod.enabled === undefined ? relationship.enabled : Boolean(mod.enabled),
            fromDisplayName:
              textValue(mod.from_display_name) ||
              contactNameByReference.get(textValue(mod.from_reference) || relationship.fromReference) ||
              relationship.fromDisplayName,
            toDisplayName:
              textValue(mod.to_display_name) ||
              contactNameByReference.get(textValue(mod.to_reference) || relationship.toReference) ||
              relationship.toDisplayName,
          }
        : {
            ...relationship,
            fromDisplayName:
              contactNameByReference.get(relationship.fromReference) || relationship.fromDisplayName,
            toDisplayName:
              contactNameByReference.get(relationship.toReference) || relationship.toDisplayName,
          };
    }),
    places,
    placeLinks: baseDraft.placeLinks.map((link) => {
      const mod = linkMods.get(link.proposalId);
      return mod
        ? {
            ...link,
            contactReference: textValue(mod.contact_reference) || link.contactReference,
            role: textValue(mod.role) || link.role,
            placeReference: textValue(mod.place_reference) || link.placeReference,
            placeName:
              textValue(mod.place_name) ||
              placeNameByReference.get(textValue(mod.place_reference) || link.placeReference) ||
              link.placeName,
            contactDisplayName:
              textValue(mod.contact_display_name) ||
              contactNameByReference.get(textValue(mod.contact_reference) || link.contactReference) ||
              link.contactDisplayName,
          }
        : {
            ...link,
            placeName: placeNameByReference.get(link.placeReference) || link.placeName,
            contactDisplayName:
              contactNameByReference.get(link.contactReference) || link.contactDisplayName,
          };
    }),
  };
}

export function contactDraftModificationSummary(modifications: ContactDraftModifications): string {
  const labels: string[] = [];
  if ((modifications.contacts || []).length > 0) labels.push('contacts');
  if ((modifications.relationships || []).length > 0) labels.push('relationships');
  if ((modifications.places || []).length > 0) labels.push('places');
  if ((modifications.contact_place_links || []).length > 0) labels.push('place links');
  return labels.join(', ');
}
