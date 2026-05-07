import { Alert, Platform } from 'react-native';
import * as ImagePicker from 'expo-image-picker';

export type ImagePickSource = 'camera' | 'library';

async function chooseImageSource(): Promise<ImagePickSource | null> {
  if (Platform.OS === 'web') {
    return 'library';
  }

  return await new Promise<ImagePickSource | null>((resolve) => {
    Alert.alert('Add photo', 'Choose where to get the picture from.', [
      {
        text: 'Take photo',
        onPress: () => resolve('camera'),
      },
      {
        text: 'Choose from library',
        onPress: () => resolve('library'),
      },
      {
        text: 'Cancel',
        style: 'cancel',
        onPress: () => resolve(null),
      },
    ], {
      cancelable: true,
      onDismiss: () => resolve(null),
    });
  });
}

export async function pickSingleImage(): Promise<ImagePicker.ImagePickerAsset | null> {
  const source = await chooseImageSource();
  if (!source) {
    return null;
  }

  if (source === 'camera') {
    const cameraPermission = await ImagePicker.requestCameraPermissionsAsync();
    if (!cameraPermission.granted) {
      throw new Error('Allow camera access to capture a photo.');
    }

    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.8,
      allowsEditing: false,
      base64: true,
    });
    return result.canceled || !result.assets?.length ? null : result.assets[0];
  }

  const libraryPermission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!libraryPermission.granted) {
    throw new Error('Allow photo library access to choose a photo.');
  }

  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ImagePicker.MediaTypeOptions.Images,
    quality: 0.8,
    allowsEditing: false,
    base64: true,
  });
  return result.canceled || !result.assets?.length ? null : result.assets[0];
}
