module.exports = {
  root: true,
  extends: ['expo', 'prettier'],
  settings: {
    'import/resolver': {
      typescript: {
        project: './mobile/tsconfig.json',
        tsconfigRootDir: process.cwd(),
        alwaysTryTypes: true,
      },
      node: {
        extensions: ['.js', '.jsx', '.ts', '.tsx'],
      },
    },
  },
};
