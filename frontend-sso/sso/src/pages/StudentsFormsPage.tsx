import React, { useState, useEffect, useCallback } from "react";

const API_BASE_URL = "https://backend.jkusa.org";

interface Field {
  id: number;
  label: string;
  field_type: string;
  required: boolean;
  position: number;
  default_value?: string;
  options?: string[];
  description?: string;
  placeholder?: string;
  help_text?: string;
  min_value?: number;
  max_value?: number;
  min_length?: number;
  max_length?: number;
  file_upload_config?: {
    allowed_types?: string[];
    max_size?: number;
    multiple?: boolean;
  };
  conditions?: Condition[];
}

interface Condition {
  depends_on_field_id: number;
  operator: string;
  value: string;
}

interface Form {
  id: number;
  title: string;
  description: string;
  open_date: string;
  close_date: string;
  status: string;
  target_all_students: boolean;
  target_years: number[];
  fields: Field[];
}

interface Submission {
  id?: number;
  form_id: number;
  student_id: number;
  data: Record<string, any>;
  submitted_at?: string;
  last_edited_at?: string;
  locked: boolean;
}

interface FormStatus {
  form_id: number;
  form_status: string;
  submission_status: string;
  is_locked: boolean;
  time_remaining_seconds: number;
  deadline: string;
  submitted_at?: string;
  last_edited_at?: string;
}

const StudentFormsPage = () => {
  const [view, setView] = useState<"list" | "detail">("list");
  const [forms, setForms] = useState<Form[]>([]);
  const [selectedForm, setSelectedForm] = useState<Form | null>(null);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [fileData, setFileData] = useState<Record<string, File[]>>({});
  const [formStatus, setFormStatus] = useState<FormStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [toastType, setToastType] = useState<"success" | "error">("success");
  const [isAuthenticated, setIsAuthenticated] = useState(true);

  const getToken = () => {
    try {
      return sessionStorage.getItem('auth_token');
    } catch (e) {
      console.error('Failed to retrieve token:', e);
      return null;
    }
  };

  const makeAuthenticatedRequest = async (url: string, options?: RequestInit) => {
    try {
      const token = getToken();
      const headers: HeadersInit = { ...options?.headers };

      if (!(options?.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
      }

      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }

      const response = await fetch(url, {
        ...options,
        headers,
        credentials: "include",
      });

      if (response.status === 401) {
        setIsAuthenticated(false);
        throw new Error("Unauthorized: Please log in again");
      }

      return response;
    } catch (error) {
      throw error;
    }
  };

  const showToast = (message: string, type: "success" | "error" = "success") => {
    setToastMessage(message);
    setToastType(type);
    setTimeout(() => setToastMessage(null), 3000);
  };

  const formatDistanceToNow = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (seconds < 0) {
      const absDays = Math.floor(-seconds / 86400);
      const absHours = Math.floor((-seconds % 86400) / 3600);
      const absMinutes = Math.floor((-seconds % 3600) / 60);
      if (absDays > 0) return `in ${absDays}d`;
      if (absHours > 0) return `in ${absHours}h`;
      if (absMinutes > 0) return `in ${absMinutes}m`;
      return "soon";
    }

    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    if (days > 0) return `${days}d ago`;
    if (hours > 0) return `${hours}h ago`;
    if (minutes > 0) return `${minutes}m ago`;
    return "now";
  };

  const formatDateTime = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  useEffect(() => {
    if (view !== "list" || !isAuthenticated) return;

    const fetchForms = async () => {
      setIsLoading(true);
      try {
        const response = await makeAuthenticatedRequest(`${API_BASE_URL}/registrations/forms?skip=0&limit=50`);

        if (response.ok) {
          const data = await response.json();
          const formsArray = Array.isArray(data) ? data : [];
          const sorted = formsArray.sort((a: Form, b: Form) => 
            new Date(b.open_date).getTime() - new Date(a.open_date).getTime()
          );
          setForms(sorted);
        } else if (response.status === 404) {
          setForms([]);
        } else {
          throw new Error("Failed to load forms");
        }
      } catch (error) {
        showToast((error as Error).message, "error");
      } finally {
        setIsLoading(false);
      }
    };

    fetchForms();
  }, [view, isAuthenticated]);

  const handleSelectForm = async (formId: number) => {
    setIsLoading(true);
    try {
      const [formResponse, submissionResponse] = await Promise.all([
        makeAuthenticatedRequest(`${API_BASE_URL}/registrations/forms/${formId}`),
        makeAuthenticatedRequest(`${API_BASE_URL}/registrations/forms/${formId}/submission`).catch(() => null),
      ]);

      if (formResponse.ok) {
        const form = await formResponse.json();
        setSelectedForm(form);

        const now = new Date();
        const closeDate = new Date(form.close_date);
        const isLocked = now > closeDate || form.status !== "open";

        setFormStatus({
          form_id: form.id,
          form_status: form.status,
          submission_status: submissionResponse && submissionResponse.ok ? "submitted" : "not_submitted",
          is_locked: isLocked,
          time_remaining_seconds: Math.max(0, Math.floor((closeDate.getTime() - now.getTime()) / 1000)),
          deadline: form.close_date,
        });

        const initialData = form.fields.reduce((acc: any, field: Field) => ({
          ...acc,
          [field.id]: field.default_value || (field.field_type === "multi_select" || field.field_type === "checkbox" ? [] : ""),
        }), {});

        if (submissionResponse && submissionResponse.ok) {
          const submissionData = await submissionResponse.json();
          setSubmission(submissionData);
          const processedData = Object.entries(submissionData.data).reduce((acc, [key, value]) => ({
            ...acc,
            [isNaN(Number(key)) ? key : Number(key)]: value,
          }), {});
          setFormData({ ...initialData, ...processedData });
        } else {
          setFormData(initialData);
          setSubmission(null);
        }
      } else {
        throw new Error("Failed to load form");
      }

      setView("detail");
    } catch (error) {
      showToast((error as Error).message, "error");
    } finally {
      setIsLoading(false);
    }
  };

  const validateForm = () => {
    const newErrors: Record<string, string> = {};

    if (selectedForm) {
      selectedForm.fields.forEach((field) => {
        if (!isFieldVisible(field)) return;

        const value = formData[field.id];
        const files = fileData[field.id];

        if (field.required) {
          if (field.field_type === 'file_upload' || field.field_type === 'multi_file_upload') {
            if (!files || files.length === 0) {
              newErrors[field.id] = `${field.label} is required`;
            }
          } else if (value === "" || value === null || value === undefined || (Array.isArray(value) && value.length === 0)) {
            newErrors[field.id] = `${field.label} is required`;
          }
        }

        if (value !== "" && value !== null && value !== undefined) {
          if (field.field_type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value))) {
            newErrors[field.id] = "Invalid email format";
          }

          if (field.field_type === "phone") {
            const cleaned = String(value).replace(/\D/g, '');
            if (cleaned.length < 7 || cleaned.length > 15) {
              newErrors[field.id] = "Invalid phone number";
            }
          }

          if (field.field_type === "number" && isNaN(Number(value))) {
            newErrors[field.id] = "Must be a number";
          }

          if (field.min_length && String(value).length < field.min_length) {
            newErrors[field.id] = `Minimum ${field.min_length} characters required`;
          }

          if (field.max_length && String(value).length > field.max_length) {
            newErrors[field.id] = `Maximum ${field.max_length} characters allowed`;
          }

          if (field.min_value !== undefined && Number(value) < field.min_value) {
            newErrors[field.id] = `Minimum value is ${field.min_value}`;
          }

          if (field.max_value !== undefined && Number(value) > field.max_value) {
            newErrors[field.id] = `Maximum value is ${field.max_value}`;
          }

          // FIXED: Validate multi_select / checkbox options
          if ((field.field_type === "multi_select" || field.field_type === "checkbox") && Array.isArray(value)) {
            const invalidOptions = value.filter(v => !field.options?.includes(v));
            if (invalidOptions.length > 0) {
              newErrors[field.id] = `Invalid option(s): ${invalidOptions.join(", ")}. Allowed: ${field.options?.join(", ")}`;
            }
          }

          if ((field.field_type === "select" || field.field_type === "radio") && field.options && !field.options.includes(value)) {
            newErrors[field.id] = `Invalid selection. Must be one of: ${field.options.join(", ")}`;
          }
        }
      });
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = useCallback(async () => {
    if (!selectedForm || submission?.locked) return;

    if (!validateForm()) {
      showToast("Please fix the errors below", "error");
      return;
    }

    setIsSaving(true);
    const formDataToSend = new FormData();

    console.log("=== FORM SUBMISSION DEBUG (Frontend) ===");
    
    selectedForm.fields.forEach((field) => {
      if (!isFieldVisible(field)) return;

      const value = formData[field.id];
      const files = fileData[field.id];

      if (field.field_type === 'file_upload' || field.field_type === 'multi_file_upload') {
        if (files && files.length > 0) {
          files.forEach(file => {
            formDataToSend.append(String(field.id), file);
            console.log(`Field ${field.id} (${field.label}): FILE - ${file.name} (${file.size} bytes)`);
          });
        }
      } else if (value !== undefined && value !== null && value !== "") {
        if (Array.isArray(value)) {
          // Only send valid options
          const validValues = value.filter(v => field.options?.includes(v));
          if (validValues.length > 0) {
            formDataToSend.append(String(field.id), JSON.stringify(validValues));
            console.log(`Field ${field.id} (${field.label}): ARRAY - ${JSON.stringify(validValues)}`);
          }
        } else if (field.field_type === "select" || field.field_type === "radio") {
          if (field.options?.includes(value)) {
            formDataToSend.append(String(field.id), String(value));
            console.log(`Field ${field.id} (${field.label}): ${String(value)}`);
          }
        } else {
          formDataToSend.append(String(field.id), String(value));
          console.log(`Field ${field.id} (${field.label}): ${String(value)}`);
        }
      }
    });

    console.log("=== FINAL FormData ENTRIES ===");
    for (const [key, value] of formDataToSend.entries()) {
      if (value instanceof File) {
        console.log(`FormData: ${key} = [File] ${value.name} (${value.size} bytes)`);
      } else {
        console.log(`FormData: ${key} = ${value}`);
      }
    }
    console.log("=== END DEBUG ===");

    try {
      const url = submission
        ? `${API_BASE_URL}/registrations/forms/${selectedForm.id}/submission`
        : `${API_BASE_URL}/registrations/forms/${selectedForm.id}/submit`;
      const method = submission ? "PUT" : "POST";

      const response = await makeAuthenticatedRequest(url, {
        method,
        body: formDataToSend,
      });

      if (response.ok) {
        const data = await response.json();
        if (data.submission_id || data.id) {
          setSubmission({
            id: data.submission_id || data.id,
            form_id: selectedForm.id,
            student_id: 0,
            data: formData,
            locked: false
          });
        }
        setLastSaved(new Date());
        setFileData({});
        showToast(submission ? "Form updated successfully" : "Form submitted successfully", "success");
      } else {
        const errorData = await response.json().catch(() => ({}));
        console.error("Error response:", errorData);
        const errorMessage = errorData.detail || `Failed to ${submission ? "update" : "submit"} form`;
        throw new Error(errorMessage);
      }
    } catch (error) {
      console.error("Submission error:", error);
      showToast((error as Error).message, "error");
    } finally {
      setIsSaving(false);
    }
  }, [selectedForm, formData, fileData, submission]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    handleSave();
  };

  const handleFileChange = (fieldId: number, files: FileList | null, field: Field) => {
    if (!files || files.length === 0) return;

    const fileArray = Array.from(files);
    const config = field.file_upload_config || {};
    const maxSize = config.max_size || 10 * 1024 * 1024;

    for (const file of fileArray) {
      if (file.size > maxSize) {
        showToast(`File ${file.name} exceeds maximum size of ${formatFileSize(maxSize)}`, "error");
        return;
      }
    }

    if (field.field_type === 'multi_file_upload') {
      setFileData(prev => ({ ...prev, [fieldId]: fileArray }));
    } else {
      setFileData(prev => ({ ...prev, [fieldId]: [fileArray[0]] }));
    }
  };

  const removeFile = (fieldId: number, index: number) => {
    setFileData(prev => {
      const files = prev[fieldId] || [];
      return {
        ...prev,
        [fieldId]: files.filter((_, i) => i !== index)
      };
    });
  };

  const isFieldVisible = (field: Field): boolean => {
    if (!field.conditions || field.conditions.length === 0) return true;

    return field.conditions.every((condition) => {
      const dependsValue = formData[condition.depends_on_field_id];

      if (condition.operator === "equals") {
        return String(dependsValue) === String(condition.value);
      } else if (condition.operator === "not_equals") {
        return String(dependsValue) !== String(condition.value);
      }

      return true;
    });
  };

  const renderField = (field: Field) => {
    if (!isFieldVisible(field)) return null;

    const isDisabled = submission?.locked;
    const value = formData[field.id];
    const files = fileData[field.id] || [];

    const inputClass = `w-full px-4 py-2 bg-white border rounded-lg text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-100 disabled:text-gray-500 ${
      errors[field.id] ? "border-red-500" : "border-gray-300"
    }`;

    const renderInput = () => {
      switch (field.field_type) {
        case "short_text":
        case "text":
          return (
            <input
              id={`field-${field.id}`}
              type="text"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              placeholder={field.placeholder}
              className={inputClass}
              disabled={isDisabled}
              minLength={field.min_length}
              maxLength={field.max_length}
            />
          );

        case "long_text":
        case "textarea":
          return (
            <textarea
              id={`field-${field.id}`}
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              placeholder={field.placeholder}
              className={`${inputClass} resize-none`}
              rows={4}
              disabled={isDisabled}
              minLength={field.min_length}
              maxLength={field.max_length}
            />
          );

        case "email":
          return (
            <input
              id={`field-${field.id}`}
              type="email"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              placeholder={field.placeholder || "email@example.com"}
              className={inputClass}
              disabled={isDisabled}
            />
          );

        case "phone":
          return (
            <input
              id={`field-${field.id}`}
              type="tel"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              placeholder={field.placeholder || "+254 700 000 000"}
              className={inputClass}
              disabled={isDisabled}
            />
          );

        case "number":
        case "currency":
          return (
            <input
              id={`field-${field.id}`}
              type="number"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              placeholder={field.placeholder}
              className={inputClass}
              disabled={isDisabled}
              min={field.min_value}
              max={field.max_value}
              step={field.field_type === "currency" ? "0.01" : "any"}
            />
          );

        case "date":
          return (
            <input
              id={`field-${field.id}`}
              type="date"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              className={inputClass}
              disabled={isDisabled}
            />
          );

        case "time":
          return (
            <input
              id={`field-${field.id}`}
              type="time"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              className={inputClass}
              disabled={isDisabled}
            />
          );

        case "datetime":
          return (
            <input
              id={`field-${field.id}`}
              type="datetime-local"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              className={inputClass}
              disabled={isDisabled}
            />
          );

        case "boolean":
          return (
            <div className="flex items-center space-x-2">
              <input
                type="checkbox"
                id={`field-${field.id}`}
                checked={value === true || value === "true"}
                onChange={(e) => setFormData({ ...formData, [field.id]: e.target.checked })}
                disabled={isDisabled}
                className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-2 focus:ring-blue-500"
              />
              <label htmlFor={`field-${field.id}`} className="text-sm font-medium text-gray-900">
                {field.label}
                {field.required && <span className="text-red-600 ml-1">*</span>}
              </label>
            </div>
          );

        case "select":
          return (
            <select
              id={`field-${field.id}`}
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              className={inputClass}
              disabled={isDisabled}
            >
              <option value="">Select an option</option>
              {field.options?.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          );

        case "radio":
          return (
            <div className="space-y-2">
              {field.options?.map((option) => (
                <label key={option} className="flex items-center space-x-2 cursor-pointer hover:bg-gray-50 p-2 rounded">
                  <input
                    type="radio"
                    name={`field-${field.id}`}
                    checked={value === option}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setFormData({ ...formData, [field.id]: option });
                      }
                    }}
                    disabled={isDisabled}
                    className="w-4 h-4 text-blue-600 border-gray-300"
                  />
                  <span className="text-sm text-gray-700">{option}</span>
                </label>
              ))}
            </div>
          );

        case "multi_select":
        case "checkbox":
          return (
            <div className="space-y-2">
              {field.options?.map((option) => (
                <label key={option} className="flex items-center space-x-2 cursor-pointer hover:bg-gray-50 p-2 rounded">
                  <input
                    type="checkbox"
                    checked={Array.isArray(value) && value.includes(option)}
                    onChange={(e) => {
                      const currentValues = Array.isArray(value) ? value : [];
                      const newValues = e.target.checked
                        ? [...currentValues, option]
                        : currentValues.filter(v => v !== option);
                      setFormData({ ...formData, [field.id]: newValues });
                    }}
                    disabled={isDisabled}
                    className="w-4 h-4 text-blue-600 border-gray-300 rounded"
                  />
                  <span className="text-sm text-gray-700">{option}</span>
                </label>
              ))}
            </div>
          );

        case "file_upload":
        case "multi_file_upload":
          const config = field.file_upload_config || {};
          const maxSize = config.max_size || 10 * 1024 * 1024;
          const allowedTypes = config.allowed_types || ['pdf', 'doc', 'image'];
          
          return (
            <div className="space-y-3">
              <div>
                <input
                  id={`field-${field.id}`}
                  type="file"
                  onChange={(e) => handleFileChange(field.id, e.target.files, field)}
                  disabled={isDisabled}
                  multiple={field.field_type === 'multi_file_upload'}
                  className="block w-full text-sm text-gray-900 border border-gray-300 rounded-lg cursor-pointer bg-white focus:outline-none"
                />
                <p className="mt-1 text-xs text-gray-500">
                  Max size: {formatFileSize(maxSize)}. Allowed: {allowedTypes.join(', ')}
                </p>
              </div>
              
              {files.length > 0 && (
                <div className="space-y-2">
                  {files.map((file, index) => (
                    <div key={index} className="flex items-center justify-between p-2 bg-gray-50 rounded border border-gray-200">
                      <div className="flex items-center space-x-2 flex-1 min-w-0">
                        <span className="text-blue-600">Attachment</span>
                        <span className="text-sm text-gray-700 truncate">{file.name}</span>
                        <span className="text-xs text-gray-500">({formatFileSize(file.size)})</span>
                      </div>
                      {!isDisabled && (
                        <button
                          type="button"
                          onClick={() => removeFile(field.id, index)}
                          className="ml-2 text-red-600 hover:text-red-800"
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );

        case "url":
          return (
            <input
              id={`field-${field.id}`}
              type="url"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              placeholder={field.placeholder || "https://example.com"}
              className={inputClass}
              disabled={isDisabled}
            />
          );

        case "rating":
          return (
            <div className="flex items-center space-x-2">
              {[1, 2, 3, 4, 5].map((rating) => (
                <button
                  key={rating}
                  type="button"
                  onClick={() => !isDisabled && setFormData({ ...formData, [field.id]: rating })}
                  disabled={isDisabled}
                  className={`text-2xl ${
                    (value || 0) >= rating ? "text-yellow-400" : "text-gray-300"
                  } hover:text-yellow-400 transition-colors disabled:cursor-not-allowed`}
                >
                  Star
                </button>
              ))}
            </div>
          );

        default:
          return (
            <input
              id={`field-${field.id}`}
              type="text"
              value={value || ""}
              onChange={(e) => setFormData({ ...formData, [field.id]: e.target.value })}
              className={inputClass}
              disabled={isDisabled}
            />
          );
      }
    };

    return (
      <div key={field.id} className={field.field_type === "boolean" ? "" : "space-y-2"}>
        {field.field_type !== "boolean" && (
          <label htmlFor={`field-${field.id}`} className="block text-sm font-medium text-gray-900">
            {field.label}
            {field.required && <span className="text-red-600 ml-1">*</span>}
          </label>
        )}
        {field.description && (
          <p className="text-xs text-gray-600 mb-2">{field.description}</p>
        )}
        {renderInput()}
        {field.help_text && !errors[field.id] && (
          <p className="text-xs text-gray-500">{field.help_text}</p>
        )}
        {errors[field.id] && (
          <div className="flex items-center gap-1 text-sm text-red-600">
            <span>Warning</span>
            <span>{errors[field.id]}</span>
          </div>
        )}
      </div>
    );
  };

  if (!isAuthenticated) {
    return (
      <div className="flex justify-center items-center h-screen bg-gray-50">
        <div className="text-center">
          <p className="text-gray-600 mb-4">You have been logged out. Please log in again.</p>
          <button onClick={() => window.location.href = "/signin"} className="px-4 py-2 bg-blue-600 text-white rounded-lg">
            Sign In
          </button>
        </div>
      </div>
    );
  }

  if (isLoading && view === "list") {
    return (
      <div className="flex justify-center items-center h-screen bg-gray-50">
        <div className="flex flex-col items-center gap-4">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
          <p className="text-gray-600">Loading forms...</p>
        </div>
      </div>
    );
  }

  if (view === "detail" && selectedForm) {
    const now = new Date();
    const closeDate = new Date(selectedForm.close_date);
    const isFormOpen = now <= closeDate && selectedForm.status === "open" && (!submission || !submission.locked);

    return (
      <div className="max-w-4xl mx-auto px-4 py-6 lg:py-8">
        {toastMessage && (
          <div className={`fixed top-4 right-4 px-6 py-3 rounded-lg shadow-lg z-50 text-white font-medium ${toastType === "success" ? "bg-green-600" : "bg-red-600"}`}>
            {toastMessage}
          </div>
        )}

        <div className="flex items-center gap-4 mb-8">
          <button onClick={() => setView("list")} className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-gray-100 transition-colors">
            <span className="text-2xl">Back</span>
          </button>
          <div>
            <h1 className="text-3xl font-bold text-gray-900">{selectedForm.title}</h1>
            <p className="text-gray-600 mt-2">{selectedForm.description}</p>
          </div>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 lg:p-8">
          {formStatus && (
            <div className={`mb-6 p-4 rounded-lg border-l-4 ${formStatus.is_locked ? "bg-red-50 border-red-400" : "bg-blue-50 border-blue-400"}`}>
              <div className="flex items-start gap-3">
                <span className="text-2xl mt-0.5">{formStatus.is_locked ? "Locked" : "Time"}</span>
                <div className="flex-1">
                  <p className={`font-medium ${formStatus.is_locked ? "text-red-800" : "text-blue-800"}`}>
                    {formStatus.is_locked ? "Form is locked (deadline passed)" : `Deadline: ${formatDateTime(formStatus.deadline)}`}
                  </p>
                  {!formStatus.is_locked && (
                    <p className={`text-sm mt-1 text-blue-700`}>
                      {formatDistanceToNow(formStatus.deadline)} remaining
                    </p>
                  )}
                  {lastSaved && (
                    <p className="text-xs text-gray-600 mt-2">
                      Last saved: {formatDistanceToNow(lastSaved.toISOString())}
                    </p>
                  )}
                  {submission?.submitted_at && (
                    <p className="text-xs text-gray-600 mt-1">
                      Submitted: {formatDateTime(submission.submitted_at)}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="space-y-6">
              {selectedForm.fields.sort((a, b) => a.position - b.position).map(renderField)}

              <div className="flex flex-wrap gap-4 pt-6 border-t border-gray-200">
                <button
                  type="submit"
                  disabled={isSaving || !isFormOpen}
                  className="flex items-center gap-2 px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 text-white font-medium rounded-lg transition-colors"
                >
                  {isSaving ? "Saving..." : submission ? "Update" : "Submit"}
                </button>

                <button
                  type="button"
                  onClick={() => setView("list")}
                  disabled={isSaving}
                  className="flex items-center gap-2 px-6 py-3 bg-white border border-gray-300 hover:bg-gray-50 text-gray-900 font-medium rounded-lg transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 lg:py-8">
      {toastMessage && (
        <div className={`fixed top-4 right-4 px-6 py-3 rounded-lg shadow-lg z-50 text-white font-medium ${toastType === "success" ? "bg-green-600" : "bg-red-600"}`}>
          {toastMessage}
        </div>
      )}

      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Available Forms</h1>
        <p className="text-gray-600">View and submit available forms</p>
      </div>

      {isLoading ? (
        <div className="flex justify-center items-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
        </div>
      ) : forms.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-xl border border-gray-100">
          <span className="text-5xl mb-4 block">Form</span>
          <p className="text-gray-600">No forms available at this time</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {forms.map((form) => {
            const isOpen = form.status === "open";
            const now = new Date();
            const closeDate = new Date(form.close_date);
            const timeRemaining = closeDate.getTime() - now.getTime();
            const isClosingSoon = timeRemaining > 0 && timeRemaining < 86400000;

            return (
              <div
                key={form.id}
                className="bg-white rounded-xl shadow-sm border border-gray-100 hover:shadow-md hover:border-gray-200 transition-all"
              >
                <div className="p-6">
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="text-lg font-bold text-gray-900 flex-1">
                      {form.title}
                    </h3>
                    <span
                      className={`px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap ml-2 ${
                        isOpen
                          ? isClosingSoon
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-green-100 text-green-700"
                          : "bg-red-100 text-red-700"
                      }`}
                    >
                      {isOpen ? (isClosingSoon ? "Closing soon" : "Open") : "Closed"}
                    </span>
                  </div>

                  <p className="text-sm text-gray-600 mb-4">{form.description}</p>

                  <div className="space-y-2 mb-6">
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                      <span>Calendar</span>
                      <span>Opens: {formatDateTime(form.open_date)}</span>
                    </div>
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                      <span>Clock</span>
                      <span>Closes: {formatDateTime(form.close_date)}</span>
                    </div>
                  </div>

                  <button
                    onClick={() => handleSelectForm(form.id)}
                    disabled={!isOpen}
                    className={`w-full py-2.5 px-4 rounded-lg font-medium transition-colors ${
                      isOpen
                        ? "bg-blue-600 hover:bg-blue-700 text-white"
                        : "bg-gray-100 text-gray-400 cursor-not-allowed"
                    }`}
                  >
                    {isOpen ? "Open Form" : "Form Closed"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default StudentFormsPage;