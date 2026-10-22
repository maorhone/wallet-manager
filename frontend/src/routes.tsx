import { Navigate, createBrowserRouter } from 'react-router-dom';
import DepartmentPage from './pages/department/DepartmentPage';
import { Shell } from './App';
import Login from './pages/Login';
import GroupPage from './pages/group/GroupPage';
import MyWallet from './pages/MyWallet';
import PlatformPage from './pages/platform/PlatformPage';
import PmoPage from './pages/pmo/PmoPage';
import ReportsPage from './pages/reports/ReportsPage';

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
      { path: 'pmo/:id', element: <Navigate to="pool" replace /> },
      { path: 'pmo/:id/:tab', element: <PmoPage /> },
      { path: 'platform', element: <Navigate to="pending" replace /> },
      { path: 'platform/:tab', element: <PlatformPage /> },
      { path: 'report/:kind/:id/:tab', element: <ReportsPage /> },
    ],
  },
]);
