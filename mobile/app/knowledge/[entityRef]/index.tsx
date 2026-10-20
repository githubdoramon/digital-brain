import { useLocalSearchParams } from 'expo-router';
import { KnowledgeScreen } from '@/components/knowledge/KnowledgeScreen';
export default function KnowledgeEntityPage() {
  const { entityRef, title } = useLocalSearchParams<{ entityRef: string; title?: string }>();
  return <KnowledgeScreen subject={entityRef} title={title || 'Knowledge'} />;
}
