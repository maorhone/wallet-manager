import { Title } from '@mantine/core';
import { Navigate, createBrowserRouter, useParams } from 'react-router-dom';
import { Shell } from './App';
import { he } from './i18n/he';
import Login from './pages/Login';
import GroupPage from './pages/group/GroupPage';
import MyWallet from './pages/MyWallet';

// The department page arrives in Step 13.
const Placeholder = ({ title }: { title: string }) => <Title order={3}>{title}</Title>;

function DepartmentPage() {
  const { id, tab } = useParams();
  return <Placeholder title={`${he.department} ${id} / ${tab}`} />;
}

export const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  {
    path: '/',
    element: <Shell />,
    children: [
      { path: 'wallet', element: <MyWallet /> },
      { path: 'group/:id', element: <Navigate to="overview" replace /> },
      { path: 'group/:id/:tab', element: <GroupPage /> },
      { path: 'department/:id', element: <Navigate to="overview" replace /> },
      { path: 'department/:id/:tab', element: <DepartmentPage /> },
      { path: 'platform', element: <Placeholder title={he.platform} /> },
    ],
  },
]);
