import React from 'react';
import DateTimePicker, { useDefaultStyles } from 'react-native-ui-datepicker';

import { theme } from '@/theme';

type LightDateTimePickerProps = React.ComponentProps<typeof DateTimePicker>;
type PickerStyles = NonNullable<LightDateTimePickerProps['styles']>;

export function LightDateTimePicker({ styles, ...props }: LightDateTimePickerProps) {
  const defaultPickerStyles = useDefaultStyles('light');

  const mergedStyles = React.useMemo<PickerStyles>(
    () => ({
      ...defaultPickerStyles,
      ...styles,
      button_prev_image: {
        ...(defaultPickerStyles.button_prev_image || {}),
        ...((styles?.button_prev_image as object) || {}),
        tintColor: theme.colors.ink,
      },
      button_next_image: {
        ...(defaultPickerStyles.button_next_image || {}),
        ...((styles?.button_next_image as object) || {}),
        tintColor: theme.colors.ink,
      },
    }),
    [defaultPickerStyles, styles],
  );

  return <DateTimePicker {...props} styles={mergedStyles} />;
}
