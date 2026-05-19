import React from 'react';

import { useTopNotice } from '@/components/top-notice';

export function useAppNotice() {
  const { showNotice } = useTopNotice();

  return React.useMemo(
    () => ({
      showNotice,
      showSuccess: (message: string) => showNotice(message, 'success'),
      showInfo: (message: string) => showNotice(message, 'info'),
      showWarning: (message: string) => showNotice(message, 'warning'),
      showError: (message: string) => showNotice(message, 'error'),
    }),
    [showNotice],
  );
}
