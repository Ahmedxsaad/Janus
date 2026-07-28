{{/*
Standard chart name, truncated to fit a Kubernetes resource-name limit (63
chars) with room for a suffix such as -serviceaccount.
*/}}
{{- define "modelguard-watch.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "modelguard-watch.fullname" -}}
{{- if .Release.Name | eq .Chart.Name -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "modelguard-watch.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "modelguard-watch.labels" -}}
helm.sh/chart: {{ include "modelguard-watch.chart" . }}
{{ include "modelguard-watch.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "modelguard-watch.selectorLabels" -}}
app.kubernetes.io/name: {{ include "modelguard-watch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "modelguard-watch.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- .Values.serviceAccount.name | default (include "modelguard-watch.fullname" .) -}}
{{- else -}}
{{- .Values.serviceAccount.name | default "default" -}}
{{- end -}}
{{- end -}}

{{/*
The Secret this Deployment reads from: whatever the caller named via
existingSecret, or this release's own if they did not.
*/}}
{{- define "modelguard-watch.secretName" -}}
{{- .Values.existingSecret | default (include "modelguard-watch.fullname" .) -}}
{{- end -}}
