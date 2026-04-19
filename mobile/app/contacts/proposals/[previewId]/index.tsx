import { useLocalSearchParams } from 'expo-router';

import { ContactDraftEditorScreen } from '@/components/contact-draft/ContactDraftEditorScreen';

type RouteParams = {
  previewId?: string;
  draftSessionId?: string;
};

export default function ContactProposalEditorRoute() {
  const params = useLocalSearchParams<RouteParams>();
  const draftSessionId = Array.isArray(params.draftSessionId)
    ? params.draftSessionId[0]
    : params.draftSessionId;

  return <ContactDraftEditorScreen sessionId={draftSessionId} />;
}
