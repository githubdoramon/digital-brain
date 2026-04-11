const HOME_TAB_BAR_TOP_PADDING = 12;
const HOME_TAB_BAR_BUTTON_SIZE = 60;
const HOME_TAB_BAR_MIN_BOTTOM_PADDING = 14;

export function getHomeTabBarClearance(bottomInset: number) {
  return (
    HOME_TAB_BAR_TOP_PADDING +
    HOME_TAB_BAR_BUTTON_SIZE +
    Math.max(bottomInset, HOME_TAB_BAR_MIN_BOTTOM_PADDING)
  );
}
