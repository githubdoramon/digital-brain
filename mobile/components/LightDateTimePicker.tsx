import React from 'react';
import DateTimePicker, { useDefaultStyles } from 'react-native-ui-datepicker';
import type { DatePickerBaseProps, Styles } from 'react-native-ui-datepicker';

import { theme } from '@/theme';

type LightDateTimePickerProps = DatePickerBaseProps & {
  styles?: Styles;
};

export function LightDateTimePicker({ styles, ...props }: LightDateTimePickerProps) {
  const defaultPickerStyles = useDefaultStyles('light');

  const mergedStyles = React.useMemo<Styles>(
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
