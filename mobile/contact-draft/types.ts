export type ContactDraftContact = {
  proposalId: string;
  reference: string;
  operation: 'create' | 'update';
  source: 'explicit' | 'derived';
  displayName: string;
  birthday: string;
  aliasesText: string;
  emailsText: string;
  phonesText: string;
  linksText: string;
  tagsText: string;
  comments: string;
};

export type ContactDraftRelationship = {
  proposalId: string;
  kind: 'explicit' | 'derived';
  fromReference: string;
  toReference: string;
  fromDisplayName: string;
  toDisplayName: string;
  relationshipType: string;
  enabled: boolean;
};

export type ContactDraftPlace = {
  proposalId: string;
  reference: string;
  name: string;
  address: string;
};

export type ContactDraftPlaceLink = {
  proposalId: string;
  contactReference: string;
  contactDisplayName: string;
  placeReference: string;
  placeName: string;
  role: string;
};

export type ContactProposalDraft = {
  contacts: ContactDraftContact[];
  relationships: ContactDraftRelationship[];
  places: ContactDraftPlace[];
  placeLinks: ContactDraftPlaceLink[];
};

export type ContactDraftModifications = {
  contacts?: Array<{
    proposal_id: string;
    reference?: string;
    display_name?: string;
    birth_date?: string | null;
    aliases?: string[];
    emails?: string[];
    phones?: string[];
    links?: string[];
    tags?: string[];
    comments?: string | null;
  }>;
  relationships?: Array<{
    proposal_id: string;
    from_reference?: string;
    to_reference?: string;
    relationship_type?: string;
    enabled?: boolean;
    from_display_name?: string;
    to_display_name?: string;
  }>;
  places?: Array<{
    proposal_id: string;
    reference?: string;
    name?: string;
    address?: string;
  }>;
  contact_place_links?: Array<{
    proposal_id: string;
    contact_reference?: string;
    contact_display_name?: string;
    place_reference?: string;
    role?: string;
    place_name?: string;
  }>;
};
