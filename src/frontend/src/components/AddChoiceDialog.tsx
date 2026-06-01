import React from 'react';
import {
  Card,
  CardActionArea,
  CardContent,
  Dialog,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import LibraryAddIcon from '@mui/icons-material/LibraryAdd';

interface AddChoiceDialogProps {
  open: boolean;
  title: string;
  subtitle: string;
  createLabel: string;
  createDescription: string;
  importLabel: string;
  importDescription: string;
  onCreateNew: () => void;
  onImportExisting: () => void;
  onCancel: () => void;
}

/**
 * The "Add Station / Add Challenge" popup. It offers two paths: build a
 * brand-new library entity, or import an existing one from the library.
 */
const AddChoiceDialog: React.FC<AddChoiceDialogProps> = ({
  open,
  title,
  subtitle,
  createLabel,
  createDescription,
  importLabel,
  importDescription,
  onCreateNew,
  onImportExisting,
  onCancel,
}) => (
  <Dialog open={open} onClose={onCancel} maxWidth="sm" fullWidth>
    <DialogTitle>{title}</DialogTitle>
    <DialogContent>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {subtitle}
      </Typography>
      <Stack spacing={2}>
        <Card variant="outlined">
          <CardActionArea onClick={onCreateNew}>
            <CardContent>
              <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
                <AddIcon color="primary" />
                <div>
                  <Typography variant="subtitle1" component="p" sx={{ fontWeight: 600 }}>
                    {createLabel}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {createDescription}
                  </Typography>
                </div>
              </Stack>
            </CardContent>
          </CardActionArea>
        </Card>
        <Card variant="outlined">
          <CardActionArea onClick={onImportExisting}>
            <CardContent>
              <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
                <LibraryAddIcon color="primary" />
                <div>
                  <Typography variant="subtitle1" component="p" sx={{ fontWeight: 600 }}>
                    {importLabel}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {importDescription}
                  </Typography>
                </div>
              </Stack>
            </CardContent>
          </CardActionArea>
        </Card>
      </Stack>
    </DialogContent>
  </Dialog>
);

export default AddChoiceDialog;
